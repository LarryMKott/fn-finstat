# 发布流程与 Release 日志

> 适用版本：0.7.0 · 整理日期：2026-09-16
> 相关文件：`.workflow/build-fpk.yml`、`scripts/ci_build.sh`、`scripts/gen_release_notes.py`、`CHANGELOG.md`

## 1. 整体流程

Push 到 `main` 分支（或 Gitee Go 界面手动触发）后，流水线按两个阶段串行执行：

| 阶段 | Step | 内容 |
| --- | --- | --- |
| 构建 | `build@nodejs` → `bash scripts/ci_build.sh` | 环境准备 → 测试门禁 → 前端构建 + fnpack 打包 → **生成 Release 日志** |
| 发布 | `release@gitee` | 创建/覆盖 Release，上传 FPK 与日志附件 |

`ci_build.sh` 内部串行四步：

1. **环境准备** — apt 换清华源、python3/pip 安装、pip 镜像加速
2. **测试门禁** — 单元测试 + `ruff check --select F,E9`（`SKIP_TESTS=1` 可跳过，仅调试用）
3. **构建打包** — Node 自举 → `npm ci` → `npm run build` → fnpack → 产物重命名
4. **发布日志** — `gen_release_notes.py` 整理提交历史，输出 `releaseNode.txt`

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
**安装**：下载附件 `fn-finstat-v0.7.0.fpk` ...
**校验（SHA-256）**：`...`
```

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

### 代价：同版本重复构建会覆盖 Release

tag 固定为应用版本号，配合 `allowUpdate: true`，**重复构建同一版本会覆盖同名 Release**，上一次的构建产物与描述随之丢失。

历史由 `CHANGELOG.md` 承担保留职责，而非靠 tag 堆积。因此：

- 发版前**必须**递增 `VERSION`，否则新包会顶掉上一版的 Release，用户也无法从 tag 区分新旧
- 已有的版本一致性门禁（`sync_version.py --check`）只查三处是否一致，**不查版本号是否递增**，这一步靠人工

### 历史遗留

仓库里早先的构建号 tag —— `v`、`v23`、`v33`、`v37`、`v38`、`v39` —— 是旧策略的产物，无法从 tag 看出应用版本。新策略从 `v0.7.0` 起生效。这些历史 tag 未清理（删除远程 tag 属破坏性操作，需人工确认后再动）。

## 4. 发版操作步骤

1. 确认 `main` 分支测试通过，且本轮提交均符合 [`docs/开发规范/Git提交规范.md`](开发规范/Git提交规范.md)
2. **修改 `VERSION` 文件**（如 `0.7.0` → `0.8.0`）——这一步同时决定 Release 的 tag，漏改会覆盖上一版
3. 运行 `python3 scripts/gen_release_notes.py --update-changelog` 生成/更新 CHANGELOG
4. 提交 `VERSION` 与 `CHANGELOG.md`，push 到 `main`
5. 流水线自动构建发布，Release 描述即为自动整理的日志

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
