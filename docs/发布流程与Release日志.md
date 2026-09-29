# 发布流程与 Release 日志

> 适用版本：1.1.2 · 整理日期：2026-09-26（2026-09-29 改：GitHub 侧工作流模块化拆分为复合动作，见 §6.1）
> 相关文件：`.workflow/build-fpk.yml`、`.workflow/build-fpk-dev.yml`、`.github/workflows/build-fpk.yml`、
> `.github/workflows/build-fpk-dev.yml`、`.github/actions/fpk/`（五个复合动作）、`scripts/ci_build.sh`、
> `scripts/build_fpk.sh`、`scripts/build_fpk.bat`、`scripts/sync_version.py`、
> `scripts/gen_release_notes.py`、`CHANGELOG.md`、`RELEASE_NOTES.md`

## 1. 整体流程

仓库有**两条发布流水线**，共用 `scripts/ci_build.sh`，差别只在构建渠道：

| 流水线 | 触发 | 渠道 | 产物 | Release |
| --- | --- | --- | --- | --- |
| `.workflow/build-fpk.yml` | push 到 `main` | `release` | `fn-finstat-latest.fpk` + `fn-finstat-v0.7.1.fpk` | 正式（tag `v0.7.1`） |
| `.workflow/build-fpk-dev.yml` | push 到 `dev` | `dev` | `fn-finstat-dev.fpk` + `fn-finstat-v0.7.1-dev.42.g1a2b3c4.fpk` | 预发布（tag `v0.7.1-dev.42.g1a2b3c4`） |

> 别名（`latest` / `dev`）是**稳定下载入口**——同一渠道每次构建都覆盖同名文件，链接可长期不变；
> 带版本号的副本用于回答"这是哪一次构建"。`fn-finstat.fpk` 是 fnpack 的原始输出，
> 只作流水线制品与构建证明，**不挂到 Release 附件**（否则页面上会出现两个内容相同的包）。

GitHub 侧有一套**语义对等的镜像工作流**（`.github/workflows/`，见 §6）：与 Gitee Go
触发条件、渠道、产物完全一致，共用同一个 `ci_build.sh`，差别只在发布接口与镜像源。

每个 Release 挂 4 个附件：

| 附件 | 用途 |
| --- | --- |
| `fn-finstat-latest.fpk` / `fn-finstat-dev.fpk` | 按渠道区分的稳定入口，直接下载即可安装 |
| `fn-finstat-v{版本}.fpk` | 带版本号副本，确认拿到的是哪一次构建 |
| `MD5SUMS.txt` | 上面两个包的 MD5。下载后在同目录执行 `md5sum -c MD5SUMS.txt` 校验完整性 |
| `releaseNode.txt` | 本次构建的分组日志与 SHA-256 |

> 📲 **应用内的「设置 → 关于 → 检查更新」直接消费这些 Release**（Gitee / GitHub 的
> `releases` 列表接口，匿名访问；**默认 GitHub 源**，可在界面切换到 Gitee ——
> 两站点的 Release API 同构，下载直链与更新说明的解析规则共用）。用户还可选
> 「正式版 / 开发版」版本线：缺省跟随本机版本（正式包看正式线、测试包看测试线），
> 显式选择后按所选线过滤。除引导浏览器下载外，还可**由后端把最新 fpk
> （优先带版本号副本——带唯一标识的交付物）下载到管理员配置的安装包保存目录**
> （接口 `POST /api/update/download`；目录在「设置 → 关于 → 版本更新」配置，
> **未配置时功能不可用并提示先配置**，绝不回退到其他目录；落盘后用 `MD5SUMS.txt`
> 校验，不匹配即丢弃）。
> 下载直链优先取附件里的**带版本号副本**
> `fn-finstat-v{版本}.fpk`（最能回答"下到的是哪一次构建"），缺失时才退回裸名或任意 fpk。
> 因此发版时有两个约束不能破：
>
> 1. **别省掉带版本号副本**（`fn-finstat-v*.fpk` 在 `assertFiles` 里）；
> 2. **正式版必须保持 `prerelease: false`** —— 应用按该标记做渠道过滤，正式渠道只看非预发布
>    Release，标记错了会直接把测试包推给正式版用户。
>
> 另外别把 `fn-finstat-v*.fpk` 改成不含三段式版本号的命名：应用侧的版本解析要求
> `major.minor.patch` 齐全，否则该 Release 会被整体跳过。

> ⚠️ **Release 描述取自入库文件 `RELEASE_NOTES.md`**（见下方 release 阶段的 `description`），
> 它只含**本次版本**那一节 —— 发布页因此等于「本次改了什么」。`CHANGELOG.md` 是累积历史，
> 两者由 `python scripts/gen_release_notes.py --update-changelog` **同批生成**，不会各自漂移。
> 应用内「检查更新」的**更新说明**正是从这个描述里截出来的。
>
> 因此**推送前必须把这两份文件重新生成并提交**（dev 渠道每次 push 都发 Release，「落后」是常态
> 而非例外，别只在大版本发布时才更新）。两条纪律：
>
> 1. **在独立提交里重生成**：`--update-changelog` 只能看到「已提交」的历史，把日志和代码混在
>    同一个提交里，这个版本的日志就会漏掉同批的代码改动（实测：`v0.7.3-dev.6` 的日志漏掉了
>    它自己携带的那个修复）。日志单独一个提交时，唯一漏掉的只是「更新日志」这一行本身。
> 2. **别手改这两个文件**：内容一律由脚本产出；CI 也会在打包前校验 `RELEASE_NOTES.md` 存在、
>    非空且对应当前版本，不符合直接失败（`ci_build.sh` 第 5.5 步）。

两条流水线都先**构建**再**发布**，各阶段串行：

| 阶段 | Step | 内容 |
| --- | --- | --- |
| 构建 | `build@nodejs` → `bash scripts/ci_build.sh` | 环境准备 → 渠道判定 → 测试门禁 → 前端构建 + fnpack 打包 → **生成 Release 日志 + 校验发布说明** |
| 发布 | `release@gitee` | 创建/覆盖 Release，上传 FPK 与日志附件 |

`ci_build.sh` 内部串行五步：

1. **环境准备** — apt 换清华源、python3/pip 安装、pip 镜像加速
2. **渠道判定** — 定渠道与产物版本号，落盘 `.local_tmp/build-version.txt` 供 yml 读取
3. **测试门禁** — 单元测试 + `ruff check --select F,E9`（`SKIP_TESTS=1` 可跳过，仅调试用）
4. **构建打包** — Node 自举 → `npm ci` → `npm run build` → fnpack → 产物重命名
5. **发布日志** — `gen_release_notes.py` 整理提交历史，输出 `releaseNode.txt`（附件用）
6. **发布说明门禁**（5.5 步）— 校验入库的 `RELEASE_NOTES.md` 存在、非空、对应当前版本

> 渠道与版本的完整规则（含为什么用 `-dev` 预发布段而不是 `+` 构建元数据）见
> [`docs/开发规范/编译与构建规范.md`](开发规范/编译与构建规范.md) 的 §1.1。

## 2. Release 日志自动生成

### 2.1 原理

`release@gitee` 插件的 `description` 接受**仓库里的文件路径**，插件读取该文件在**流水线运行时那个
commit** 里的内容作为 Release 描述正文：

```yaml
description: RELEASE_NOTES.md        # 只含本次版本的发布说明（入库文件）
```

两条由此推出的硬约束：

| 约束 | 原因 |
| --- | --- |
| **描述源必须是入库文件** | 插件从仓库代码里读，读不到构建期生成、被 `.gitignore` 排除的文件。`releaseNode.txt` 正属后者 —— 构建 #42/#44 两次验证：`description: "文本 \| releaseNode.txt"` 的组合语法与直接指向该文件都不生效 |
| **描述源只应含本次版本** | 累积文件（如 `CHANGELOG.md`）贴到发布页，读者第一眼看到的可能是几个版本之前的日志。dev 渠道每次 push 都发 Release，撞得最明显 |

所以描述用 `RELEASE_NOTES.md`（只含本次版本，与 `CHANGELOG.md` 同批生成），构建期生成的
`releaseNode.txt` 则作为 **Release 附件**保留（本次构建的分组日志 + SHA-256）。

> Release 插件只识别内置变量（如 `${GITEE_PIPELINE_BUILD_NUMBER}`），**读不到 `VERSION` 文件的内容**。所以真实应用版本号只能经由入库文件的内容呈现，这也是日志必须自动生成、而不是在 yml 里拼接的原因。

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

# 更新入库的两份：CHANGELOG.md（累积）+ RELEASE_NOTES.md（只含本次版本）
python3 scripts/gen_release_notes.py --update-changelog

# 指定范围与版本
python3 scripts/gen_release_notes.py --from v0.6.0 --to HEAD --tag 0.7.0
```

**用法纪律**（两条，都来自实际踩坑）：

1. **在独立提交里重生成**。脚本只能看到「已提交」的历史，所以日志要和代码改动分开提交 ——
   混在同一个提交里，这个版本的日志会漏掉同批代码（实测 `v0.7.3-dev.6` 的日志漏掉了它自己
   携带的那个修复）。日志单独一个提交时，唯一漏掉的只是「更新日志」这一行本身。
   顺序：**先提交代码与文档 → 再跑 `--update-changelog` → 提交日志 → push**。
2. **同版本重复生成不会截断段落**。段落里记了 `<!-- release-start: <sha> -->`，重复运行会沿用
   该起点，因此「每次推送前都重生成」是安全的（否则段落只剩自上次生成以来的提交 —— 实测
   0.7.3 段落从 15 个提交缩成 2 个）。要强制换起点用 `--from`。

推荐在**提升 `VERSION` 后、打发版提交前**运行一次 `--update-changelog`，把两份日志随代码一起提交，这样下次流水线就能自动定位基线。

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
3. 运行 `python3 scripts/gen_release_notes.py --update-changelog` 生成/更新 `CHANGELOG.md` 与 `RELEASE_NOTES.md`
4. 提交 `VERSION`、`CHANGELOG.md`、`RELEASE_NOTES.md`，push 到 `main`
5. 流水线自动构建发布，Release 描述即为 `RELEASE_NOTES.md` 的内容（本次版本）

> 步骤 3 必须在步骤 4 之前、且日志与代码分开提交（见 §2.4 用法纪律）：描述取自入库文件，
> 与代码混在一个提交里会让本版日志漏掉同批改动。

### 4.2 测试版（`dev`）

push 到 `dev` 即自动构建并发布**预发布** Release。**不需要改 `VERSION`**（测试版号 = 当前
`VERSION` + 构建期附加的 `-dev` 段）；但要**在推送前重生成日志并单独提交**，否则发布页与
应用内的更新说明会停留在上一次生成时的提交上：

```bash
# 1) 先提交代码/文档改动
# 2) 再重生成日志（沿用该版本已记录的起点，不会截断段落）
python3 scripts/gen_release_notes.py --update-changelog
git add CHANGELOG.md RELEASE_NOTES.md
git commit -m "docs(changelog): 更新变更日志与发布说明"
# 3) 推送
git push origin dev
# → build-fpk-dev.yml 自动运行
# → 产物 fn-finstat-dev.fpk（稳定入口）
#        fn-finstat-v0.7.3-dev.7.g1a2b3c4.fpk（带版本号副本）
# → Release「fn-finstat v0.7.3-dev.7.g1a2b3c4（测试版）」，标记为预发布
```

要点：

- **不要为测试版修改 `VERSION`**——测试版号是「当前 `VERSION` + 构建期附加的 `-dev` 段」，
  改 `VERSION` 会连带影响正式发版的版本线。**唯一例外**：要让设备上的「检查更新」识别出
  「有新版本」时，必须把 `VERSION` 往前推一档（更新判定要求远端版本严格大于本机版本，
  同一版本线上重发是测不出来的）
- 想在设备上验证又不想等 CI，本地打同款包：

  ```bash
  BUILD_CHANNEL=dev bash scripts/build_fpk.sh
  ```

- 测试包与正式包**共用同一条版本线**：装了 `0.7.1-dev.x` 后，正式 `0.7.1` 可直接覆盖升级
  （semver 中预发布版本小于同号正式版）；反过来从 `0.7.1` 装 `0.7.1-dev.x` 属于降级，
  设备可能拒绝，需先卸载
- 测试版的 Release 描述同样取自 `RELEASE_NOTES.md`，但日志里会列出「上次发版至今」的提交，
  包含尚未发版的开发中变更——这正是测试版要验证的内容

## 5. 排查清单

| 现象 | 原因与处理 |
| --- | --- |
| **构建失败：`RELEASE_NOTES.md` 不是本次版本** | 描述源没跟上版本线。跑 `python scripts/gen_release_notes.py --update-changelog` 并提交（`ci_build.sh` 5.5 步的硬门禁，故意不放过） |
| **Release 描述只有兜底文本** | `RELEASE_NOTES.md` 未被插件读到（未入库 / 路径写错）。注意描述源是**入库文件**，不是构建期生成的 `releaseNode.txt` |
| Release 描述里混着历史版本的日志 | `description` 被改回了 `CHANGELOG.md`（累积文件）。改回 `RELEASE_NOTES.md` |
| 本版日志漏掉同批改动 | 日志与代码提交在同一个 commit 里 —— 脚本看不到未提交的历史。日志要单独提交（见 §2.4） |
| 同版本重生成后段落变短 | 用了 `--from` 覆盖了段落记录的起点，或该段落还没有 `<!-- release-start -->` 标记（旧格式）。正常重生成会沿用已记录起点 |
| **tag 变成 `v` 或 `v0.0.0-unknown`** | `${APP_VERSION}` 未生效，说明 `GITEE_PARAMS` 没有从 build 阶段传过去。先确认 build 日志里有 `==> 应用版本：0.7.0`；若写入正常但 tag 仍退化，回退方案是配 `GITEE_ACCESS_TOKEN` 并用 curl 调 OpenAPI 建 tag |
| 新版本的 Release 顶掉了上一版 | `VERSION` 没递增，tag 相同导致覆盖（`allowUpdate: true`）。改 `VERSION` 后重新发布 |
| 日志里出现"最近 30 个提交" | 基线缺失（无 CHANGELOG、无语义化 tag），提交一次 CHANGELOG 即可 |
| 日志提交数与预期不符 | CI 浅克隆；确认 `git fetch --unshallow` 是否成功，或改用 `--from` 显式指定 |
| 附件上传失败 | 确认 `assertFiles` 中文件确实存在于工作目录；`ci_build.sh` 已做非空兜底 |
| 日志分组不对 | 提交信息未按约定式提交格式；无法识别的会进「其他变更」，不会丢 |
| dev 流水线没触发 | 确认 push 到的是 `dev` 分支本身——触发正则为 `^dev$`，`feature/dev-xxx` 之类的分支不会触发 |
| 测试版 Release 被当成正式版展示 | 确认 `.workflow/build-fpk-dev.yml` 里 `prerelease: true` 还在（Gitee 据此把它排除在「最新版本」外） |
| 应用内「检查更新」把正式版用户引向测试包 | 渠道过滤失效。确认测试版 Release 的 `prerelease: true` 未被改掉；确认正式流水线 `APP_VERSION` 不含 `-dev`（应用由本机版本号推导渠道） |
| 应用内「检查更新」显示有新版本但没有下载按钮 | 该 Release 附件里没有 fpk（只剩校验文件 / 源码包）。对照上文 §1 的附件清单补齐，下载直链按「带版本号副本 → 裸名 → 任意 fpk」取 |
| 应用内「检查更新」看不到某个已发布的版本 | 该 Release 的 tag 不是三段式版本号（如 `v23`、`v0.7`），被应用侧解析跳过了 |
| 检查更新失败提示「接口限流」 | GitHub 匿名额度按 IP 每小时 60 次，多账号共用出口 IP 时容易耗尽。在「设置 → 关于」把源切到 Gitee（国内网络也更稳），或等额度恢复后重新检查；失败结果同样缓存 5 分钟，不会每次进设置页都重试 |
| 「下载到 NAS」提示尚未配置保存目录 | 该功能只在管理员显式配置过的目录里落盘（应用级共享，与账单目录同策略）。管理员到「设置 → 关于 → 版本更新」填入 NAS 上的绝对路径并保存；目录需真实存在，不可访问时同样拒绝下载 |
| 「下载到 NAS」提示 MD5 校验不匹配 | 传输残缺或附件被替换，后端已丢弃本次下载。直接重试一次；连续失败时核对 Release 附件与 `MD5SUMS.txt` 是否配套（见 §1 的附件清单） |
| GitHub 正式版工作流失败：tag v{版本} 已存在且指向其他提交 | 忘了递增 `VERSION` 就往 `main` 推了。GitHub 侧没有 Gitee 的 `allowUpdate` 静默覆盖——tag 被占用时**故意硬失败**，防止新代码的包挂到旧版本号的 Release 上。递增 `VERSION` 并更新发布说明后重推 |
| GitHub 的 dev push 没有发布 Release | 确认推送的是 `dev` 分支本身（工作流只精确匹配 `dev`）；PR 触发的运行**只构建不发版**，产物去 Actions 运行页的 Artifacts 里拿 |

## 6. GitHub Actions（GitHub 平台的镜像流水线）

`.github/workflows/` 下两条工作流与 Gitee Go 双流水线**语义对等**：同样的触发条件、同样的
渠道与产物、同一个 `scripts/ci_build.sh`。两端共用一套构建逻辑，差异被压缩到「发布接口 +
镜像源」两点，任何一端的语义改动都必须同步另一端。

| Gitee Go（`.workflow/`） | GitHub Actions（`.github/workflows/`） | 触发 | 渠道 | Release |
| --- | --- | --- | --- | --- |
| `build-fpk.yml` | `build-fpk.yml` | push `main` / 手动 | `release` | 正式，tag `v1.1.0`，`--latest` 标记 |
| `build-fpk-dev.yml` | `build-fpk-dev.yml` | push `dev`、PR、手动 | `dev` | 预发布，tag `v1.1.0-dev.42.g…`；**PR 只构建不发版**，产物走 Actions 制品 |

与 Gitee 版的实现差异（改代码前先读这五条）：

1. **环境变量桥接**：`ci_build.sh` 与 `gen_release_notes.py` 先认平台无关的
   `CI_BRANCH` / `CI_BUILD_NUMBER` / `CI_SHORT_SHA`，再认 Gitee 的 `GITEE_*` 同义变量，
   最后退回 git 推断。GitHub 工作流只注入 `CI_BUILD_NUMBER`（取 `github.run_number`），
   短 sha 与分支靠 git 兜底；两个平台的流水线 yml 都显式传 `BUILD_CHANNEL`，不走 auto 判定。
2. **镜像源覆盖**：脚本默认国内镜像（TUNA / npmmirror），GitHub runner 上访问官方源更快，
   工作流用环境变量覆盖：`PIP_INDEX_URL=https://pypi.org/simple`、
   `NPM_REGISTRY=https://registry.npmjs.org`。fnpack 仍从 fnnas.com 官方静态源下载，没有镜像。
3. **发布用 gh CLI**（runner 自带，不引第三方 Action）：`gh release create` 显式
   `--target $GITHUB_SHA` 锚定构建提交，`--notes-file RELEASE_NOTES.md` 与 Gitee 的
   `description` 同源；已存在的 tag 走 `gh release upload --clobber` 覆盖附件。
4. **tag 守卫比 Gitee 严格**：Gitee 的 `allowUpdate: true` 允许同版本静默覆盖（tag 仍指旧
   提交，附件被换成新代码的包——页面与内容错位且不报错）。GitHub 侧正式版工作流在
   tag 指向其他提交时**硬失败**并提示递增 `VERSION`；只有 tag 恰好指向本次提交（同构建
   重跑）才放行覆盖。
5. **并发策略**：dev 工作流 `cancel-in-progress: true`（新 push 顶掉在途构建，发布在最后
   一步，中途取消不留残余）；正式版 `cancel-in-progress: false`（发布绝不并发取消，排队串行）。

### 6.1 GitHub 侧的模块化布局（复合动作）

两条 GitHub 工作流的步骤实现抽成了五个仓库内复合动作（composite actions），
工作流文件只剩**管道编排与入口语义**（触发、并发、权限、渠道取值、PR 不发版）：

| 复合动作 | 职责 | 关键约定 |
| --- | --- | --- |
| `.github/actions/fpk/setup-build-env` | Python 3.12 + Node 24（pip/npm 缓存） | 版本锚点与 Gitee Go 同源；**checkout 不在其中** |
| `.github/actions/fpk/run-build` | 按渠道运行 `ci_build.sh` | 显式传渠道；覆盖为官方镜像源 |
| `.github/actions/fpk/verify-version` | 渠道校验；release 附加 tag 守卫 | 输出 `version`，是下游两个动作的唯一版本来源 |
| `.github/actions/fpk/upload-artifact` | 制品命名与清单（按渠道分派） | PR 运行的取包入口 |
| `.github/actions/fpk/publish-release` | gh release create/upload | `--latest` / `--prerelease` 按渠道分派 |

两条结构性约束，改动前必读：

1. **checkout 必须内联在工作流里且排在所有本地动作之前**：仓库本地复合动作的
   文件本身就在仓库里，runner 工作区初始为空，未检出无法引用。
2. **版本号走 `verify-version` 的输出显式传参**（`steps.verify.outputs.version`），
   不走 `GITHUB_ENV` 隐式通道——数据流向在 yml 里一眼可见，也避免两条通道打架。

> 镜像一致性约定：`ci_build.sh` 是唯一构建逻辑；「渠道校验」作为平台侧双保险
> 保留在 `verify-version` 动作中（dev 版本号必含 `-dev` 段 / release 版本号必不含），
> 从 Gitee yml 原样移植，不得省略。Gitee Go 没有复合动作机制，`.workflow/` 两条
> yml 保持单体写法。改动流程语义时，`.workflow/` 与 `.github/`（工作流 + 动作）
> 同批修改、同批提交。
| 应用内「检查更新」不显示更新说明，或说明里的版本号与提示的新版本对不上 | `RELEASE_NOTES.md` 的小节不是当前版本（最常见：推送前忘了跑 `gen_release_notes.py --update-changelog`）。应用侧按基版本匹配小节，匹配不上就隐藏说明而不会错标，所以现象是「说明区为空」；补跑脚本后重新构建即可。注：正常情况下构建阶段的门禁会先把这种状态拦下来 |
| 正式发版日志少了中间若干变更 | dev tag 被当成了发版基线。检查 `gen_release_notes.py` 的 `SEMVER_TAG_RE` 是否仍以 `$` 锚定、不接受 `-dev` 后缀（有单元测试锁死） |
| 测试版产物名不含 `-dev` | 渠道没生效。dev 流水线的 build step 应显式 `BUILD_CHANNEL=dev bash scripts/ci_build.sh`；该流水线会硬断言版本号含 `-dev` 后才会继续 |
| Release 附件只有别名与版本号副本、没有裸名 `fn-finstat.fpk` | 设计如此。裸名与别名内容完全相同，一起挂上去只会让人犹豫该下哪个；它仍作为流水线制品保留 |
| Release 附件里的 `MD5SUMS.txt` 只列了两个包 | 设计如此：只列实际交付的产物。裸名不上传，列它会让用户找不着文件 |
| 下载后 `md5sum -c MD5SUMS.txt` 报 `No such file or directory` | 要么文件名被改过（浏览器可能加 `(1)` 后缀），要么校验文件被写成了 CRLF。前者重命名即可，后者见构建规范排查表 |
