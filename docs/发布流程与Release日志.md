# 发布流程与 Release 日志

> 适用版本：0.7.1 · 整理日期：2026-09-17
> 相关文件：`.workflow/build-fpk.yml`、`.workflow/build-fpk-dev.yml`、`scripts/ci_build.sh`、
> `scripts/build_fpk.sh`、`scripts/build_fpk.bat`、`scripts/sync_version.py`、
> `scripts/gen_release_notes.py`、`CHANGELOG.md`

## 1. 整体流程

仓库有**两条发布流水线**，共用 `scripts/ci_build.sh`，差别只在构建渠道：

| 流水线 | 触发 | 渠道 | 产物 | Release |
| --- | --- | --- | --- | --- |
| `.workflow/build-fpk.yml` | push 到 `main` | `release` | `fn-finstat-latest.fpk` + `fn-finstat-v0.7.1.fpk` | 正式（tag `v0.7.1`） |
| `.workflow/build-fpk-dev.yml` | push 到 `dev` | `dev` | `fn-finstat-dev.fpk` + `fn-finstat-v0.7.1-dev.42.g1a2b3c4.fpk` | 预发布（tag `v0.7.1-dev.42.g1a2b3c4`） |

> 别名（`latest` / `dev`）是**稳定下载入口**——同一渠道每次构建都覆盖同名文件，链接可长期不变；
> 带版本号的副本用于回答"这是哪一次构建"。`fn-finstat.fpk` 是 fnpack 的原始输出，
> 只作流水线制品与构建证明，**不挂到 Release 附件**（否则页面上会出现两个内容相同的包）。

每个 Release 挂 4 个附件：

| 附件 | 用途 |
| --- | --- |
| `fn-finstat-latest.fpk` / `fn-finstat-dev.fpk` | 按渠道区分的稳定入口，直接下载即可安装 |
| `fn-finstat-v{版本}.fpk` | 带版本号副本，确认拿到的是哪一次构建 |
| `MD5SUMS.txt` | 上面两个包的 MD5。下载后在同目录执行 `md5sum -c MD5SUMS.txt` 校验完整性 |
| `releaseNode.txt` | 本次构建的分组日志与 SHA-256 |

两条流水线都先**构建**再**发布**，各阶段串行：

| 阶段 | Step | 内容 |
| --- | --- | --- |
| 构建 | `build@nodejs` → `bash scripts/ci_build.sh` | 环境准备 → 渠道判定 → 测试门禁 → 前端构建 + fnpack 打包 → **生成 Release 日志** |
| 发布 | `release@gitee` | 创建/覆盖 Release，上传 FPK 与日志附件 |

`ci_build.sh` 内部串行五步：

1. **环境准备** — apt 换清华源、python3/pip 安装、pip 镜像加速
2. **渠道判定** — 定渠道与产物版本号，落盘 `.local_tmp/build-version.txt` 供 yml 读取
3. **测试门禁** — 单元测试 + `ruff check --select F,E9`（`SKIP_TESTS=1` 可跳过，仅调试用）
4. **构建打包** — Node 自举 → `npm ci` → `npm run build` → fnpack → 产物重命名
5. **发布日志** — `gen_release_notes.py` 整理提交历史，输出 `releaseNode.txt`

> 渠道与版本的完整规则（含为什么用 `-dev` 预发布段而不是 `+` 构建元数据）见
> [`docs/开发规范/编译与构建规范.md`](开发规范/编译与构建规范.md) 的 §1.1。

## 2. Release 日志自动生成

### 2.1 原理

`release@gitee` 插件的 `description` 支持 **`兜底文本 | 文件路径`** 语法：

```yaml
description: "兜底文本 | releaseNode.txt"
```

插件优先读取工作目录下 `releaseNode.txt` 的内容作为 Release 描述正文；文件缺失时才回落到竖线前的文本。因此 `ci_build.sh` 只需在打包后把日志写进 `releaseNode.txt`，并把它随构建产物一起传给发布阶段。

> Release 插件只识别内置变量（如 `${GITEE_PIPELINE_BUILD_NUMBER}`），**读不到 `VERSION` 文件的内容**。所以真实应用版本号只能经由 `releaseNode.txt` 的文件内容呈现，这也是日志必须自动生成、而不是在 yml 里拼接的原因。

### 2.2 日志内容

`scripts/gen_release_notes.py` 从 Git 历史整理，输出结构：

```
## fn-finstat v0.7.0（构建 #40）

> 📅 发布日期：2026-09-16 · 🔢 提交数量：30 · 👥 贡献者：xxx

### 🔒 安全修复
### ✨ 新功能
### 🐛 问题修复
### ⚡ 性能优化
### ♻️ 代码重构
### 🧪 测试
### 👷 构建与流水线
### 📦 打包构建
### 📝 文档
### 🎨 样式调整
### 🔧 杂项维护
### ⏪ 版本回退
### 💥 破坏性变更   （提交带 ! 或 BREAKING CHANGE 时）
### 📌 其他变更     （不符合约定式提交的提交）

---
**安装**：下载附件 `fn-finstat-latest.fpk`（该渠道的固定入口）或 `fn-finstat-v0.7.0.fpk`（本次构建），在飞牛 OS 应用中心手动安装。
**校验（MD5）**：下载附件 `MD5SUMS.txt`，与 fpk 放在同一目录后执行 `md5sum -c MD5SUMS.txt`（macOS 用 `md5 -c MD5SUMS.txt`）。
**校验（SHA-256）**：`...`
**变更范围**：`...`
```

> **dev 渠道的产物**：标题形如 `## fn-finstat v0.7.1-dev.42.g1a2b3c4（构建 #42）`，
> 并在标题下多一行 `> 🧪 **测试版本**：由 dev 分支自动构建，仅供验证使用，请勿作为正式版本分发。`
> 附件名同样带 `-dev`。这行声明由 `gen_release_notes.py --channel dev` 生成——
> Release 附件可以直接下载安装，用户往往只看正文不看 tag，没有这行就分不清测试包与正式包。

> **安装与校验指引**：说明末尾固定给出"下载哪个附件 + 怎么校验"，由
> `gen_release_notes.py --alias <渠道别名> --sha256 <值>` 生成（CI 从 `CHANNEL_ALIAS` 传入）。
> 附件列表里孤零零一个 `MD5SUMS.txt`，不写清用途就没人会用——
> **发布了校验文件却不告诉用户怎么用，等于没发**。

分组规则遵循[约定式提交](https://www.conventionalcommits.org/zh-hans/)（type 别名做了兼容，如 `feature`→新功能、`bugfix`/`hotfix`→问题修复）。**无法识别的提交不会被丢弃**，统一归入「其他变更」。

**完整的 type 取值、scope 推荐值与正反示例见 [`docs/开发规范/Git提交规范.md`](开发规范/Git提交规范.md)**——Release 日志的质量完全取决于提交信息是否规范，两者是同一套规则。

### 2.3 基线推断（"上次发版至今"）

为了让日志只含本次增量而不是全量历史，脚本按顺序推断起点：

| 优先级 | 来源 | 说明 |
| --- | --- | --- |
| 1 | `--from <ref>` | 命令行显式指定 |
| 2 | `CHANGELOG.md` 的 `<!-- release-baseline: <sha> -->` | 上次发版记录的位置，正常流程走这条 |
| 3 | 最近的语义化版本 tag（形如 `v1.2.3`） | 会跳过 `v39` 这类构建号 tag |
| 4 | 最近 30 个提交 | 兜底，可用 `--limit` 调整 |

每次生成后都会把当前 HEAD 写入 baseline 标记，供下次发版使用。

**浅克隆兼容**：Gitee Go 可能只拉浅历史，脚本会检测并在 `ci_build.sh` 中先尝试 `git fetch --unshallow`；若历史仍不完整，自动退化为最近 N 个提交，不会让发布失败。

### 2.4 本地用法

```bash
# 预览本次将要生成的 Release 日志
python3 scripts/gen_release_notes.py

# 生成 releaseNode.txt（CI 用，本地一般不需要）
python3 scripts/gen_release_notes.py -o releaseNode.txt

# 发版时更新 CHANGELOG.md（同版本重复运行会覆盖，幂等）
python3 scripts/gen_release_notes.py --update-changelog

# 指定范围与版本
python3 scripts/gen_release_notes.py --from v0.6.0 --to HEAD --tag 0.7.0
```

推荐在**提升 `VERSION` 后、打发版提交前**运行一次 `--update-changelog`，把 CHANGELOG 随代码一起提交，这样下次流水线就能自动定位基线。

### 2.5 失败兜底

日志生成属于"锦上添花"，不应阻断发布。三层保护：

1. `gen_release_notes.py` 内部对 git 调用全面容错，拿不到信息就降级
2. `ci_build.sh` 中脚本失败 → 回退为原始 `git log --oneline` 列表
3. 最终仍为空 → 写入一行最简标题，插件自动回落到 yml 的兜底文本

## 3. 版本号与 Tag 策略

### 机制

**应用版本号唯一真实来源是 `VERSION` 文件**，三处产物都由它派生：

| 产物 | 同步方式 |
| --- | --- |
| 包内 `manifest` 与 `app/config.py` | `scripts/sync_version.py` 打包时覆写暂存副本 |
| `frontend/package.json` | 同上（源码树，需单独 `--sync-frontend`） |
| **Release 的 `tagName`（`v0.7.0`）** | build 阶段写入 `GITEE_PARAMS`，release 阶段以 `${APP_VERSION}` 引用 |

`GITEE_PARAMS` 是 Gitee Go 原生的跨阶段传参机制（[官方文档](http://help.gitee.com/gitee-go/pipeline/parameter)）：在前一个 step 执行 `echo 'Key=Value' >> GITEE_PARAMS`，后续阶段即可用 `${Key}` 引用。插件的 `tagName` / `releaseName` 支持引用这类流水线级变量。

```bash
# build 阶段（.workflow/build-fpk.yml）
echo "APP_VERSION=${APP_VERSION}" >> GITEE_PARAMS
```

```yaml
# release 阶段
tagName: v${APP_VERSION}
releaseName: fn-finstat v${APP_VERSION}
```

**不需要任何访问令牌**，也不用调 OpenAPI。

> ⚠️ 不要用 `##vso[task.setvariable variable=X]Y`。那是 Azure DevOps 的语法，Gitee Go 不识别。历史提交 `f12443a` 曾用它在 build 阶段传版本号，失败后 `395cd64` 回退并得出「自定义变量无法传递给 plugin step」的结论——**该结论是错的**，真实原因是用错了平台的语法。

### dev 渠道的 tag 与「最新版本」

dev 流水线的 `tagName` 同样是 `v${APP_VERSION}`，但那里的 `APP_VERSION` 已带预发布段，
结果是 `v0.7.1-dev.42.g1a2b3c4`——**与正式 tag 天然隔离，绝不会覆盖正式 Release**。

配合 `prerelease: true`：

- 测试版 Release 不会被 Gitee 标为「最新版本」，仓库首页仍引导用户下载正式版
- 每个构建号一个 tag，同号重复构建才会覆盖（`allowUpdate: true`）

**代价：dev tag 会随提交堆积**。每次 push 到 `dev` 都产生一个 tag，长期看列表会变长。
当前接受这个代价，因为可追溯性（每个测试包对应哪个 commit 一目了然）比 tag 列表整洁更重要。
若要收敛，可把 dev 流水线的 `tagName` 改成固定值（如 `dev-latest`）+ `allowUpdate: true` 覆盖——
代价是 tag 指向首次创建时的 commit，与实际构建内容不再对应。

> dev tag **不会被当作发版基线**：`gen_release_notes.py` 的 `SEMVER_TAG_RE` 用 `$` 锚定、
> 不接受预发布后缀（有单元测试锁死）。否则正式版日志的起点会落在"最后一次测试构建"上，
> 中间合入 `main` 的变更会从日志里凭空消失。

### 代价：同版本重复构建会覆盖 Release

tag 固定为应用版本号，配合 `allowUpdate: true`，**重复构建同一版本会覆盖同名 Release**，上一次的构建产物与描述随之丢失。

历史由 `CHANGELOG.md` 承担保留职责，而非靠 tag 堆积。因此：

- 发版前**必须**递增 `VERSION`，否则新包会顶掉上一版的 Release，用户也无法从 tag 区分新旧
- 已有的版本一致性门禁（`sync_version.py --check`）只查三处是否一致，**不查版本号是否递增**，这一步靠人工

### 历史遗留

仓库里早先的构建号 tag —— `v`、`v23`、`v33`、`v37`、`v38`、`v39` —— 是旧策略的产物，无法从 tag 看出应用版本。新策略从 `v0.7.0` 起生效。这些历史 tag 未清理（删除远程 tag 属破坏性操作，需人工确认后再动）。

## 4. 发版操作步骤

### 4.1 正式发版（`main`）

1. 确认 `main` 分支测试通过，且本轮提交均符合 [`docs/开发规范/Git提交规范.md`](开发规范/Git提交规范.md)
2. **修改 `VERSION` 文件**（如 `0.7.0` → `0.8.0`）——这一步同时决定 Release 的 tag，漏改会覆盖上一版
3. 运行 `python3 scripts/gen_release_notes.py --update-changelog` 生成/更新 CHANGELOG
4. 提交 `VERSION` 与 `CHANGELOG.md`，push 到 `main`
5. 流水线自动构建发布，Release 描述即为自动整理的日志

### 4.2 测试版（`dev`）

push 到 `dev` 即自动构建并发布**预发布** Release，**不需要改 `VERSION`，也不需要动 CHANGELOG**：

```bash
git push origin dev
# → build-fpk-dev.yml 自动运行
# → 产物 fn-finstat-dev.fpk（稳定入口）
#        fn-finstat-v0.7.1-dev.42.g1a2b3c4.fpk（带版本号副本）
# → Release「fn-finstat v0.7.1-dev.42.g1a2b3c4（测试版）」，标记为预发布
```

要点：

- **不要为测试版修改 `VERSION`**——测试版号是「当前 `VERSION` + 构建期附加的 `-dev` 段」，
  改 `VERSION` 会连带影响正式发版的版本线
- 想在设备上验证又不想等 CI，本地打同款包：

  ```bash
  BUILD_CHANNEL=dev bash scripts/build_fpk.sh
  ```

- 测试包与正式包**共用同一条版本线**：装了 `0.7.1-dev.x` 后，正式 `0.7.1` 可直接覆盖升级
  （semver 中预发布版本小于同号正式版）；反过来从 `0.7.1` 装 `0.7.1-dev.x` 属于降级，
  设备可能拒绝，需先卸载
- 测试版的 Release 描述同样取自 `CHANGELOG.md`，但日志里会列出「上次发版至今」的提交，
  包含尚未发版的开发中变更——这正是测试版要验证的内容

## 5. 排查清单

| 现象 | 原因与处理 |
| --- | --- |
| Release 描述只有兜底文本 | `releaseNode.txt` 未随产物传到发布阶段；确认它在 `FPK_ARTIFACT` 的 `path` 里 |
| **tag 变成 `v` 或 `v0.0.0-unknown`** | `${APP_VERSION}` 未生效，说明 `GITEE_PARAMS` 没有从 build 阶段传过去。先确认 build 日志里有 `==> 应用版本：0.7.0`；若写入正常但 tag 仍退化，回退方案是配 `GITEE_ACCESS_TOKEN` 并用 curl 调 OpenAPI 建 tag |
| 新版本的 Release 顶掉了上一版 | `VERSION` 没递增，tag 相同导致覆盖（`allowUpdate: true`）。改 `VERSION` 后重新发布 |
| 日志里出现"最近 30 个提交" | 基线缺失（无 CHANGELOG、无语义化 tag），提交一次 CHANGELOG 即可 |
| 日志提交数与预期不符 | CI 浅克隆；确认 `git fetch --unshallow` 是否成功，或改用 `--from` 显式指定 |
| 附件上传失败 | 确认 `assertFiles` 中文件确实存在于工作目录；`ci_build.sh` 已做非空兜底 |
| 日志分组不对 | 提交信息未按约定式提交格式；无法识别的会进「其他变更」，不会丢 |
| dev 流水线没触发 | 确认 push 到的是 `dev` 分支本身——触发正则为 `^dev$`，`feature/dev-xxx` 之类的分支不会触发 |
| 测试版 Release 被当成正式版展示 | 确认 `.workflow/build-fpk-dev.yml` 里 `prerelease: true` 还在（Gitee 据此把它排除在「最新版本」外） |
| 正式发版日志少了中间若干变更 | dev tag 被当成了发版基线。检查 `gen_release_notes.py` 的 `SEMVER_TAG_RE` 是否仍以 `$` 锚定、不接受 `-dev` 后缀（有单元测试锁死） |
| 测试版产物名不含 `-dev` | 渠道没生效。dev 流水线的 build step 应显式 `BUILD_CHANNEL=dev bash scripts/ci_build.sh`；该流水线会硬断言版本号含 `-dev` 后才会继续 |
| Release 附件只有别名与版本号副本、没有裸名 `fn-finstat.fpk` | 设计如此。裸名与别名内容完全相同，一起挂上去只会让人犹豫该下哪个；它仍作为流水线制品保留 |
| Release 附件里的 `MD5SUMS.txt` 只列了两个包 | 设计如此：只列实际交付的产物。裸名不上传，列它会让用户找不着文件 |
| 下载后 `md5sum -c MD5SUMS.txt` 报 `No such file or directory` | 要么文件名被改过（浏览器可能加 `(1)` 后缀），要么校验文件被写成了 CRLF。前者重命名即可，后者见构建规范排查表 |
