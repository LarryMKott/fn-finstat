#!/bin/bash
# CI 自包含构建脚本：环境准备 → 测试门禁 → 构建打包
# 适用于 Gitee Go 或其他 Linux CI 环境
#
# 流程：
#   1. 环境准备 — apt 换清华源 + python3/pip 安装 + pip 加速配置
#   2. 渠道判定 — 按分支定构建渠道与产物版本号（release / dev）
#   3. 测试门禁 — 安装测试依赖 + 单元测试 + ruff 静态检查 + black 格式检查
#   4. 构建打包 — Node 自举 + 前端 lint/测试门禁 + 前端构建 + fnpack 打包 + 产物重命名
#                （构建脚本只用 Python 标准库，无需 pip 装包）
#   5. 发布日志 — gen_release_notes.py 整理提交历史，输出 releaseNode.txt
#
# 可用环境变量：
#   SKIP_TESTS=1     跳过测试门禁（仅限紧急调试）
#   NODE_VERSION     Node 版本（默认 24.18.0）
#   FNPACK_VERSION    fnpack 版本（默认 1.2.3）
#   NPM_REGISTRY      npm 镜像源（默认 npmmirror）
#   BUILD_CHANNEL    构建渠道：release / dev / auto（默认 auto，按 GITEE_BRANCH 判定）
#                    main → release（版本号取 VERSION 原值）
#                    其他分支 → dev（版本号追加 -dev.{构建号}.g{短sha} 测试版后缀）
#
# 产物版本号统一由 scripts/sync_version.py 派生，并写入
# .local_tmp/build-version.txt 供流水线 yml 读取（避免 yml 再算一遍导致两处漂移）。
set -e
cd "$(dirname "$0")/.."

# ============================================================
# 1. 环境准备
# ============================================================

# pip 国内加速：清华 TUNA 镜像 + 固定缓存目录
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-pypi.tuna.tsinghua.edu.cn}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$HOME/.cache/pip}"

# apt 换源 + 安装 python3/pip
setup_apt_mirror() {
  if [ ! -f /etc/os-release ]; then return; fi
  . /etc/os-release
  case "$ID" in
    ubuntu|debian)
      if grep -q "mirrors.tuna.tsinghua.edu.cn" /etc/apt/sources.list 2>/dev/null; then
        return
      fi
      local codename="${VERSION_CODENAME:-}"
      [ -z "$codename" ] && codename="$(lsb_release -cs 2>/dev/null || echo stable)"
      echo "==> 切换 apt 源到清华镜像（$ID $codename）"
      [ ! -f /etc/apt/sources.list.bak ] && cp /etc/apt/sources.list /etc/apt/sources.list.bak 2>/dev/null || true
      if [ "$ID" = "ubuntu" ]; then
        cat > /etc/apt/sources.list <<EOF
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ $codename main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ $codename-updates main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ $codename-security main restricted universe multiverse
EOF
      else
        cat > /etc/apt/sources.list <<EOF
deb https://mirrors.tuna.tsinghua.edu.cn/debian/ $codename main
deb https://mirrors.tuna.tsinghua.edu.cn/debian/ $codename-updates main
deb https://mirrors.tuna.tsinghua.edu.cn/debian-security/ $codename-security main
EOF
      fi
      ;;
  esac
}

echo "==> 环境检查与准备"

if ! command -v python3 >/dev/null 2>&1; then
  echo "==> python3 缺失，尝试安装"
  setup_apt_mirror
  apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv \
    || yum install -y -q python3 python3-pip \
    || { echo "错误：无法安装 python3"; exit 1; }
fi
if ! python3 -m pip --version >/dev/null 2>&1; then
  echo "==> pip 缺失，尝试安装"
  setup_apt_mirror
  apt-get update -qq && apt-get install -y -qq python3-pip \
    || yum install -y -q python3-pip \
    || { echo "错误：无法安装 pip"; exit 1; }
fi

echo "==> python3: $(python3 --version)"
echo "==> pip: $(python3 -m pip --version)"
echo "==> PIP_INDEX_URL: ${PIP_INDEX_URL}"

PYTHON="$(command -v python3)"

# ============================================================
# 2. 构建渠道判定与产物版本号
# ============================================================
# 渠道决定产物版本号的形态，进而决定：产物文件名、包内 manifest/config.py、
# Release tag 与名称、Release 日志标题——测试包因此在每一处都带着 -dev 标识，
# 用户不会把 dev 构建误当正式版安装。
BUILD_CHANNEL="${BUILD_CHANNEL:-auto}"
if [ "$BUILD_CHANNEL" = "auto" ]; then
  BRANCH="${GITEE_BRANCH:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)}"
  case "$BRANCH" in
    main|master) BUILD_CHANNEL="release" ;;
    *)
      BUILD_CHANNEL="dev"
      echo "⚠️  分支 ${BRANCH} 按测试版本构建（版本号带 -dev 后缀）"
      ;;
  esac
fi
if [ "$BUILD_CHANNEL" != "release" ] && [ "$BUILD_CHANNEL" != "dev" ]; then
  echo "❌ 未知构建渠道：${BUILD_CHANNEL}（可选 release / dev / auto）"
  exit 1
fi

BUILD_NUMBER="${GITEE_PIPELINE_BUILD_NUMBER:-}"
SHORT_SHA="${GITEE_SHORT_COMMIT:-$(git rev-parse --short=7 HEAD 2>/dev/null || true)}"
BUILD_VERSION="$("$PYTHON" scripts/sync_version.py --print \
  --channel "$BUILD_CHANNEL" --build-number "$BUILD_NUMBER" --short-sha "$SHORT_SHA")"
if [ -z "$BUILD_VERSION" ]; then
  echo "❌ 无法确定产物版本号（渠道 ${BUILD_CHANNEL}）"
  exit 1
fi
# 渠道别名（release → latest，dev → dev）：产物文件名的稳定下载入口。
# 与版本号同源派生，绝不在这里硬编字符串。
CHANNEL_ALIAS="$("$PYTHON" scripts/sync_version.py --print-alias --channel "$BUILD_CHANNEL")"
if [ -z "$CHANNEL_ALIAS" ]; then
  echo "❌ 无法确定渠道别名（渠道 ${BUILD_CHANNEL}）"
  exit 1
fi
echo "==> 构建渠道：${BUILD_CHANNEL} · 产物版本：${BUILD_VERSION} · 别名：${CHANNEL_ALIAS}"

# 传给 build_fpk.sh（它优先采用已存在的 BUILD_VERSION / CHANNEL_ALIAS，
# 保证包内版本与产物命名同源），并落盘给流水线 yml 读取——yml 不再自己
# cat VERSION，避免两处算法漂移。
mkdir -p .local_tmp
printf '%s\n' "$BUILD_VERSION" > .local_tmp/build-version.txt
export BUILD_CHANNEL BUILD_VERSION BUILD_NUMBER SHORT_SHA CHANNEL_ALIAS

# ============================================================
# 3. 测试门禁（SKIP_TESTS=1 可跳过）
# ============================================================
if [ "${SKIP_TESTS:-0}" != "1" ]; then
  # 安装测试依赖
  echo "==> 安装测试依赖"
  "$PYTHON" -m pip install --disable-pip-version-check \
    -r app/requirements.txt pytest httpx ruff black \
    || "$PYTHON" -m pip install --disable-pip-version-check --break-system-packages \
      -r app/requirements.txt pytest httpx ruff black

  # 单元测试
  echo "==> 单元测试门禁"
  bash scripts/run_tests.sh

  # ruff 静态检查（只拦 F + E9 真问题）
  echo "==> 静态检查门禁：ruff check --select F,E9"
  ruff check app cmd scripts tests --select F,E9 || {
    echo "❌ 静态检查未通过"
    exit 1
  }

  # black 格式检查（配置见 pyproject.toml；黑只做格式，不改语义）
  echo "==> 格式门禁：black --check"
  black --check app cmd scripts tests || {
    echo "❌ 存在未格式化的文件，请本地执行：black app cmd scripts tests"
    exit 1
  }

  # 版本号一致性（VERSION 是唯一来源，前端 package.json 必须跟随）
  echo "==> 版本号一致性门禁"
  "$PYTHON" scripts/sync_version.py --check || {
    echo "❌ 版本号不一致，请执行 python3 scripts/sync_version.py --sync-frontend"
    exit 1
  }
  echo "==> 测试门禁全部通过 ✅"
fi

# ============================================================
# 4. 构建打包
# ============================================================
NODE_VERSION="${NODE_VERSION:-24.18.0}"
FNPACK_VERSION="${FNPACK_VERSION:-1.2.3}"
FNPACK_URL="${FNPACK_URL:-https://static2.fnnas.com/fnpack/fnpack-${FNPACK_VERSION}-linux-amd64}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"

# Node 自举（Gitee Go build@nodejs 应已自带，但兜底以防 PATH 问题）
need_node=1
if command -v node >/dev/null 2>&1; then
  major="$(node -v | sed 's/^v\([0-9]*\).*/\1/')"
  [ "${major:-0}" -ge 18 ] && need_node=0
fi
if [ "$need_node" = 1 ]; then
  echo "==> bootstrap Node v${NODE_VERSION}"
  mkdir -p /tmp/node24
  curl -fsSL "https://npmmirror.com/mirrors/node/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.gz" \
    | tar -xz -C /tmp/node24 --strip-components=1
  export PATH="/tmp/node24/bin:$PATH"
fi
# 兜底：确保 npm 在 PATH 中
if ! command -v npm >/dev/null 2>&1; then
  export PATH="/tmp/node24/bin:$PATH"
fi
echo "==> Node: $(node -v)  npm: $(npm -v)"

# 前端构建
echo "==> frontend build"
cd frontend
npm config set registry "$NPM_REGISTRY"
npm ci --no-fund --no-audit || {
  echo "❌ npm ci 失败（锁文件不一致或依赖解析失败）"
  echo "   请本地执行 npm install 并提交更新后的 package-lock.json"
  exit 1
}

# 前端质量门禁（与后端 ruff / 测试门禁对等）
# 注意：不把 prettier --check 放进门禁——它有强烈的重排倾向，
# 会让 CI 因纯格式化差异而红，噪音大于收益；格式化交给编辑器保存时自动完成。
echo "==> 前端 lint 门禁：eslint ."
npm run lint || {
  echo "❌ ESLint 未通过（本地可执行 cd frontend && npm run lint:fix 自动修复）"
  exit 1
}
echo "==> 前端测试门禁：node --test"
npm test || {
  echo "❌ 前端测试未通过"
  exit 1
}

npm run build
cd ..

# fnpack
FNPACK_BIN="${HOME}/.cache/fnpack/fnpack"
mkdir -p "$(dirname "$FNPACK_BIN")"
if [ ! -x "$FNPACK_BIN" ]; then
  echo "==> download fnpack v${FNPACK_VERSION}"
  curl -fsSL "$FNPACK_URL" -o "$FNPACK_BIN"
  chmod +x "$FNPACK_BIN"
fi

# 打包（构建脚本只用 Python 标准库，pip 无需装包；SKIP_TESTS=1 避免重复跑测试）
# BUILD_CHANNEL / BUILD_VERSION 已 export，build_fpk.sh 直接采用，保证包内版本
# 与这里后续的产物命名、Release tag 完全同源。
echo "==> fnpack build"
PYTHON="$PYTHON" FNPACK="$FNPACK_BIN" SKIP_TESTS=1 bash scripts/build_fpk.sh

# 产物校验：裸名 + 渠道别名 + 带版本号副本（build_fpk.sh 已按 BUILD_VERSION /
# CHANNEL_ALIAS 生成后两者，这里只兜底补一份，并计算 SHA-256 供 Release 日志引用）
echo "==> 构建产物：$(pwd)/fn-finstat.fpk"
FPK_SHA256="$(sha256sum fn-finstat.fpk | awk '{print $1}')"
echo "==> SHA-256：${FPK_SHA256}"
FPK_ALIAS="fn-finstat-${CHANNEL_ALIAS}.fpk"
FPK_VERSIONED="fn-finstat-v${BUILD_VERSION}.fpk"
[ -f "$FPK_ALIAS" ] || cp fn-finstat.fpk "$FPK_ALIAS"
[ -f "$FPK_VERSIONED" ] || cp fn-finstat.fpk "$FPK_VERSIONED"
echo "==> 渠道别名：${FPK_ALIAS}（渠道 ${BUILD_CHANNEL}）"
echo "==> 带版本号副本：${FPK_VERSIONED}"

# MD5 校验文件：覆盖实际交付的两个产物。这里无条件重算一遍（幂等）——
# 上面的兜底 cp 有可能改了文件，重算能保证校验值与最终交付物一定一致。
rm -f MD5SUMS.txt
"$PYTHON" scripts/gen_checksums.py -o MD5SUMS.txt "$FPK_ALIAS" "$FPK_VERSIONED"
echo "==> MD5 校验文件：MD5SUMS.txt（$(wc -l < MD5SUMS.txt) 个产物）"

# ============================================================
# 5. 生成 Release 说明（releaseNode.txt）
#    release@gitee 的 description 支持 "兜底文本 | 文件路径" 语法，
#    会读取该文件内容作为 Release 描述，因此这里先把它生成出来。
#    日志生成失败不能阻断发布，故有任何异常都回退为原始提交列表。
# ============================================================
echo "==> 生成 Release 说明"
# 先删旧文件：构建若在此步之前被中断（超时 kill、磁盘满），
# 残留的上一轮 releaseNode.txt 会被发布阶段当成本次日志上传
# ——与 fnpack 先删旧 fpk 是同一类防护
rm -f releaseNode.txt
# Gitee Go 可能是浅克隆，缺少历史会导致无法推断"上次发版到哪"，先尝试补全
git fetch --unshallow --tags >/dev/null 2>&1 || git fetch --tags >/dev/null 2>&1 || true

if ! "$PYTHON" scripts/gen_release_notes.py \
      --output releaseNode.txt \
      --tag "${BUILD_VERSION}" \
      --channel "${BUILD_CHANNEL}" \
      --alias "${FPK_ALIAS}" \
      --sha256 "${FPK_SHA256}" 2>&1; then
  echo "⚠️ 结构化日志生成失败，回退为原始提交列表"
  {
    echo "## fn-finstat v${BUILD_VERSION}"
    echo ""
    echo "> ⚠️ 自动整理日志失败，以下为最近提交的原始列表"
    echo ""
    git log --no-merges -20 --pretty="- %s (%h)" 2>/dev/null || true
    echo ""
    echo "---"
    echo ""
    echo "**安装**：下载附件 \`${FPK_ALIAS}\`，在飞牛 OS 应用中心手动安装。"
    echo "**校验（MD5）**：下载附件 \`MD5SUMS.txt\`，与 fpk 同目录执行 \`md5sum -c MD5SUMS.txt\`。"
  } > releaseNode.txt
fi

# 兜底：确保文件非空，否则 release 插件会回落到 yml 里的兜底描述
if [ ! -s releaseNode.txt ]; then
  {
    echo "## fn-finstat v${BUILD_VERSION}（更新日志生成异常，详见构建日志）"
    echo ""
    echo "**安装**：下载附件 \`${FPK_ALIAS}\`，在飞牛 OS 应用中心手动安装。"
    echo "**校验（MD5）**：下载附件 \`MD5SUMS.txt\`，与 fpk 同目录执行 \`md5sum -c MD5SUMS.txt\`。"
  } > releaseNode.txt
fi
echo "==> Release 说明预览："
head -20 releaseNode.txt

# ============================================================
# 5.5 Release 描述门禁（RELEASE_NOTES.md）
#     描述取的是**入库文件** RELEASE_NOTES.md（见 yml 的 description），
#     不是上面这份构建期生成的 releaseNode.txt（插件读仓库代码，读不到它）。
#     因此它必须已提交、非空、且对应本次版本 —— 否则发布页会静默变成空描述，
#     或者显示上一个版本的日志（实测踩过：v0.7.3-dev.6 的页面里第一眼是
#     v0.7.1 的日志）。这类缺陷不会让构建失败，只会让页面出错，所以必须硬门禁。
# ============================================================
BASE_VERSION="${BUILD_VERSION%%-*}"
if [ ! -s RELEASE_NOTES.md ]; then
  echo "❌ 缺少发布说明 RELEASE_NOTES.md（Release 描述的数据源）" >&2
  echo "   修复：python scripts/gen_release_notes.py --update-changelog" >&2
  echo "         然后把 RELEASE_NOTES.md 与 CHANGELOG.md 一起提交" >&2
  exit 1
fi
if ! grep -q "^## fn-finstat v${BASE_VERSION}$" RELEASE_NOTES.md; then
  echo "❌ RELEASE_NOTES.md 不是本次版本（v${BASE_VERSION}）的发布说明" >&2
  echo "   实际首行：$(head -1 RELEASE_NOTES.md)" >&2
  echo "   修复：python scripts/gen_release_notes.py --update-changelog" >&2
  echo "         然后把 RELEASE_NOTES.md 与 CHANGELOG.md 一起提交" >&2
  exit 1
fi
echo "==> 发布说明确认：$(head -1 RELEASE_NOTES.md)"

echo "==> 构建打包完成 ✅"
