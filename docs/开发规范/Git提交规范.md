# Git 提交规范

> 适用范围：全仓库 · Release 日志由提交历史自动生成，**提交信息即对外可见的发布文档**
> 配套：[发布流程与Release日志.md](../发布流程与Release日志.md) · [scripts/gen_release_notes.py](../../scripts/gen_release_notes.py)

## 1. 格式

```
<type>(<scope>): <subject>

[正文：解释"为什么"]

[脚注：Closes #42]
```

采用[约定式提交](https://www.conventionalcommits.org/zh-hans/)。

| 要求 | 规则 |
| --- | --- |
| `type` | 必填，**纯英文小写字母**，取值见第 2 节 |
| `scope` | 可选，英文小写，见第 3 节；**不得写版本号** |
| 冒号 | 英文 `:` 或中文 `：`，**冒号后必须有空格** |
| `subject` | 必填非空，不以句号结尾 |
| 标题长度 | ≤ 50 字，最多 72 字符 |
| 语言 | `type` / `scope` 英文，`subject` 中文 |
| `scope` 括号 | **必须半角 `()`**，全角 `（）` 不被识别 |

脚本实际正则（`gen_release_notes.py::COMMIT_RE`）：

```
^([A-Za-z]+)(\(([^)]*)\))?(!)?\s*[:：]\s*(.+)$
```

> **格式合法 ≠ 能进对分组**：正则放行任意字母 type（为兼容别名），但只有第 2 节列出的值才会归组；表外 type（如 `xxx:`）虽格式合法仍落入「📌 其他变更」。带数字的 type（如 `chore2:`）直接匹配失败。

## 2. type 与日志分组

| type | 日志分组 | 版本位 |
| --- | --- | --- |
| `security` | 🔒 安全修复 | patch（严重时 minor） |
| `feat` | ✨ 新功能 | **minor** |
| `fix` | 🐛 问题修复 | patch |
| `perf` | ⚡ 性能优化 | patch |
| `refactor` | ♻️ 代码重构 | 无 |
| `test` | 🧪 测试 | 无 |
| `ci` | 👷 构建与流水线 | 无 |
| `build` | 📦 打包构建 | 无 |
| `docs` | 📝 文档 | 无 |
| `style` | 🎨 样式调整 | 无 |
| `chore` | 🔧 杂项维护 | 无 |
| `revert` | ⏪ 版本回退 | 视内容 |

**别名**（脚本可识别，与标准 type 在日志中等价，但**统一用标准值**）：

| 别名 | 归入 |
| --- | --- |
| `feature`、`features` | `feat` |
| `bugfix`、`hotfix`、`fixes` | `fix` |
| `sec` | `security` |
| `performance` | `perf` |
| `refactoring` | `refactor` |
| `tests` | `test` |
| `doc` | `docs` |
| `chores`、`deps` | `chore` |

## 3. scope

| scope | 适用范围 |
| --- | --- |
| `api` | `app/api/` 接口层 |
| `core` | `app/core/` 中间件、权限、上下文、异常 |
| `db` | `app/db/` 数据层与迁移 |
| `service` | `app/services/` 业务服务层 |
| `ai` | AI 报告 |
| `import` | 账单导入、目录监听、差异报告 |
| `nas` | 飞牛 NAS 授权与系统集成 |
| `auth` | 登录、身份、权限校验 |
| `bill` / `category` / `budget` / `stat` | 对应业务域 |
| `automation` | 定时任务、自动化 |
| `frontend` | `frontend/` 前端功能 |
| `ui` | 纯视觉/交互调整 |
| `ci` | `.workflow/` 流水线配置 |
| `build` | `scripts/` 打包构建脚本 |
| `spec` | 开发规范、设计文档、接口约定 |
| `test` | 测试代码 |
| `deps` | 依赖变更 |

表外 scope 允许使用，但须是稳定模块名。禁止：`v0.5`（是版本号）、`release`（用 `chore(release)`）、`misc`/`other`/`tmp`（无信息量）。

跨模块改动可省略 scope。

## 4. subject

- 陈述做了什么：`新增…`、`修复…`、`统一…`
- 禁止无信息量描述（"修改了xx文件"、"更新代码"）
- 不以句号结尾
- 不翻 diff 即可理解改动

## 5. 破坏性变更

在 type 或 scope 后加 `!`：

```
feat(api)!: 统一错误响应结构，移除旧版 code 字段
```

带 `!` 的提交**额外进入 💥 破坏性变更 分组并置顶**，同时保留在原分组。

> 判定**只看标题的 `!` 标记**，不读正文的 `BREAKING CHANGE:`；标题里出现该字样也不会被误判。

## 6. 正文与脚注

- 正文解释**为什么**（动机、取舍），不复述代码；每行 ≤ 72 字符，与标题空一行
- 脚注关联 Issue：`Closes #42`、`Refs #38`

## 7. 提交粒度

| 原则 | 说明 |
| --- | --- |
| 原子提交 | 一个提交只做一件事，可独立回滚 |
| 单一 type | 不把新功能与重构混在一起，否则日志分组失真 |
| 可运行 | 每个提交都应通过测试（CI 有门禁） |
| 先拆后提 | 顺手的格式/lint/删死代码单独提交 |

## 8. 禁止提交

| 禁止项 | 原因 |
| --- | --- |
| 密钥、Token、密码、`.env` | 安全红线 |
| `*.fpk`、`dist/`、`app/static/`、`releaseNode.txt` | 构建产物（已在 `.gitignore`） |
| `node_modules/`、`app/venv/`、`__pycache__/` | 依赖与缓存 |
| `.idea/`、`.vscode/` | 本地 IDE 配置 |
| 大文件、临时脚本、调试代码 | 污染历史 |
| 无意义信息（`wip`、`修改`、`更新`、`111`） | 会原样出现在 Release 日志 |

## 9. 分支与合并

| 分支 | 用途 |
| --- | --- |
| `main` | 主干，**push 即触发 Gitee Go 构建并发布 Release** |
| `dev` | 开发集成分支 |
| `feature/*`、`fix/*` | 功能/修复分支 |

1. `main` 推送即发布，推送前确认 `VERSION` 与测试状态
2. 合入 `main` 用 **squash merge**，一个 PR 只留一条提交
3. squash 后的合并标题**也必须符合本规范**（它会成为 Release 日志的一行）
4. `Merge branch 'dev' into main` 这类标题会落入「📌 其他变更」

## 10. 版本号与发版

**`VERSION` 文件是版本号唯一真实来源**，不要手改 `manifest`、`app/config.py`、`frontend/package.json`（由 `scripts/sync_version.py` 同步）。

语义化版本：破坏性变更 → `MAJOR`；`feat` → `MINOR`；`fix`/`perf` → `PATCH`；`security` → `PATCH`（严重时 `MINOR`）。

```bash
echo "0.8.0" > VERSION
python3 scripts/sync_version.py --sync-frontend                # 同步 frontend/package.json
python3 scripts/gen_release_notes.py --update-changelog        # 更新 CHANGELOG.md 与 RELEASE_NOTES.md
git add VERSION frontend/package.json CHANGELOG.md RELEASE_NOTES.md
git commit -m "chore(release): 发布 0.8.0"
git push origin main
```

预览日志：`python3 scripts/gen_release_notes.py [--from v0.7.0]`

## 11. 检查清单

- [ ] 标题符合 `<type>(<scope>): <subject>`
- [ ] `type` 在第 2 节表内
- [ ] 破坏性变更已加 `!`
- [ ] 一个提交只做一件事
- [ ] 未提交密钥/产物/依赖目录
- [ ] `bash scripts/run_tests.sh` 通过
- [ ] `ruff check app cmd scripts tests --select F,E9` 通过
- [ ] `black --check app cmd scripts tests` 通过
- [ ] 需要时已同步 `VERSION`

## 12. 示例

```
✅ feat(import): 差异报告支持导出 CSV
✅ fix(security): 账单目录绝对路径不再回传给前端
✅ fix(concurrency): 迁移与备份恢复统一互斥锁
✅ perf(ci): 国内加速 Python 环境部署
✅ docs(spec): 新增开发规范
✅ revert(ci): 回退 VERSION 变更触发
✅ feat(api)!: 统一错误响应结构

❌ update VERSION.                     → chore(release): 更新版本号至 0.7.0
❌ 新增发布者名称                       → docs(manifest): 补充发布者信息
❌ 修改了几个文件 / wip / 修复bug       → 无 type 或无信息量
❌ feat(v0.5): 导出 CSV                → scope 不应是版本号
❌ Merge branch 'dev' into main        → squash 后重写标题
```

## 13. 提交模板（可选）

```bash
git config commit.template .gitmessage
```

钩子校验（默认不启用，需团队统一 `git config core.hooksPath .githooks`）：正则须与 `COMMIT_RE` 一致，即 `^[A-Za-z]+(\([^)]*\))?!?[[:space:]]*[:：][[:space:]]*.+$`——**不能只放行 12 个标准 type**，否则 `feature`/`hotfix` 等合法别名会被误拦。
