# Git 提交规范

> 版本：v1.0 · 整理日期：2026-09-16 · 适用范围：fn-finstat 全仓库（含 `app/`、`frontend/`、`scripts/`、`docs/`）
>
> 配套文档：[`docs/发布流程与Release日志.md`](../发布流程与Release日志.md)
> 配套脚本：[`scripts/gen_release_notes.py`](../../scripts/gen_release_notes.py)

## 1. 为什么要规范提交信息

本项目的 Release 日志**完全由提交历史自动生成**——`scripts/gen_release_notes.py` 会解析 `上次发版至今` 的提交，按 `type` 分组后直接写进 Gitee Release 页面描述和 `CHANGELOG.md`。

这意味着：

- **提交信息就是发布对外可见的产品文档**，会被真实用户看到
- 符合规范的提交 → 进入对应分组（✨ 新功能、🐛 问题修复…）
- **不符合规范的提交不会被丢弃**，但会统一落入 **📌 其他变更**——在 Release 页面里相当显眼

所以规范提交信息不是形式主义，而是"想让用户看到什么"的直接控制手段。

## 2. 提交信息格式

采用[约定式提交](https://www.conventionalcommits.org/zh-hans/)（Conventional Commits）：

```
<type>(<scope>): <subject>

[正文：可选，解释"为什么"而非"做了什么"]

[脚注：可选，关联 Issue / 声明破坏性变更]
```

示例：

```
feat(import): 差异报告支持导出 CSV

导入结果页新增导出按钮，便于用户在 Excel 中核对差异明细。
复用前端已有的 Blob 下载封装，不引入新依赖。

Closes #42
```

### 2.1 硬性格式要求

| 要求 | 说明 |
| --- | --- |
| `type` | **必须**为 2.2 表中的值，纯英文小写 |
| `scope` | 可选，英文小写，见 2.3 推荐值；**不要写版本号**（如 `v0.5`） |
| 冒号 | 英文 `:` 或中文 `：` 均可，冒号后**必须有空格** |
| `subject` | 必填，不能为空 |
| 标题行长度 | 建议 ≤ 50 字，最多 72 字符 |
| 结尾 | 标题行**不要**以句号结尾 |
| 语言 | 中文表述即可，`type`/`scope` 保持英文 |

> 格式校验的真实正则（脚本实现）：`^[A-Za-z]+(\([^)]*\))?!?\s*[:：]\s*(.+)$`
> 也就是说：`type` 只能是英文字母，`scope` 内不能出现右括号 `)`。

### 2.2 type 与 Release 日志分组的对应关系

**这张表是本规范的核心**——左边是你写的 `type`，右边是它会出现在 Release 日志的哪个标题下。

| type | 含义 | 日志分组 | 版本号影响 |
| --- | --- | --- | --- |
| `security` | 安全修复（越权、脱敏、SSRF 等） | 🔒 安全修复 | patch（严重时 minor） |
| `feat` | 新功能 | ✨ 新功能 | **minor** |
| `fix` | 缺陷修复 | 🐛 问题修复 | patch |
| `perf` | 性能优化 | ⚡ 性能优化 | patch |
| `refactor` | 重构（不改变外部行为） | ♻️ 代码重构 | 无 |
| `test` | 测试相关 | 🧪 测试 | 无 |
| `ci` | 流水线 / CI 配置 | 👷 构建与流水线 | 无 |
| `build` | 打包构建脚本 | 📦 打包构建 | 无 |
| `docs` | 文档 | 📝 文档 | 无 |
| `style` | 代码样式（不影响逻辑） | 🎨 样式调整 | 无 |
| `chore` | 杂项维护、依赖升级 | 🔧 杂项维护 | 无 |
| `revert` | 回退之前的提交 | ⏪ 版本回退 | 视回退内容而定 |

无法识别的 `type` 一律进入 **📌 其他变更**。

**脚本兼容的别名**（能识别但不推荐，请优先用标准 type）：

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

### 2.3 推荐的 scope

按项目实际模块划分，优先使用下列值：

| scope | 适用范围 |
| --- | --- |
| `api` | `app/api/` 接口层 |
| `core` | `app/core/` 中间件、权限、上下文、异常处理 |
| `db` | `app/db/` 数据层与迁移 |
| `service` | `app/services/` 业务服务层 |
| `ai` | AI 报告相关 |
| `import` | 账单导入、目录监听、差异报告 |
| `nas` | 飞牛 NAS 授权与系统集成 |
| `auth` | 登录、身份、权限校验 |
| `bill` / `category` / `budget` / `stat` | 对应业务域 |
| `automation` | 定时任务、自动化 |
| `frontend` | `frontend/` 前端功能 |
| `ui` | 纯视觉/交互调整 |
| `ci` | `.workflow/` 流水线配置 |
| `build` | `scripts/` 打包构建脚本 |
| `test` | 测试代码 |
| `deps` | 依赖变更 |

**不推荐**的 scope 写法：

- ❌ `v0.5`、`1.2.0` —— scope 不是版本号，版本由 `VERSION` 文件管理
- ❌ `release` —— 发版动作请改用 `chore(release)` 或 `build`
- ❌ `misc`、`other`、`tmp` —— 没有信息量，不如不写

一个提交只涉及单一模块时写 scope；跨模块重构可省略 scope（如 `refactor: 统一响应结构`）。

### 2.4 subject 写法

- 陈述"做了什么"，用祈使/陈述语气：`新增…`、`修复…`、`统一…`
- 不要写"修改了xx文件"、"更新代码"这类无信息量的描述
- 不要以句号结尾
- 能从 subject 直接看懂改动，让人不必翻 diff

### 2.5 破坏性变更

在 `type` 或 `scope` 后加 `!`：

```
feat(api)!: 统一错误响应结构，移除旧版 code 字段
```

带 `!` 的提交会**单独进入 💥 破坏性变更 分组**并置顶，同时保留在原分组中。

> ⚠️ 重要：日志脚本**只扫描提交标题行**，不会去读正文里的 `BREAKING CHANGE:`。
> 因此声明破坏性变更**必须**用 `!` 标记在标题上，写在正文里不会被识别。

### 2.6 正文与脚注

- 正文解释**为什么**这样改（动机、背景、取舍），而非复述代码
- 每行 ≤ 72 字符，与标题之间空一行
- 脚注用于关联 Issue：`Closes #42`、`Refs #38`

## 3. 提交粒度

| 原则 | 说明 |
| --- | --- |
| **原子提交** | 一个提交只做一件事，可独立回滚 |
| **单一 type** | 不要把新功能和重构混在一个提交里，否则日志分组必然失真 |
| **可运行** | 每个提交都应通过测试；CI 有测试门禁，失败会阻断构建 |
| **先拆后提** | 顺手改的格式、lint、删除死代码，请单独提交 |

拆分示例——把「新增导出功能 + 顺手重构」拆成两个提交：

```
feat(import): 差异报告支持导出 CSV
refactor(service): 提取导入结果转换为共享助手
```

## 4. 禁止提交的内容

| 禁止项 | 原因 |
| --- | --- |
| 密钥、Token、密码、`.env` 内容 | 安全红线，泄露即事故 |
| `*.fpk`、`dist/`、`app/static/assets/`、`releaseNode.txt` | 构建产物，已在 `.gitignore` 中 |
| `node_modules/`、`app/venv/`、`__pycache__/` | 依赖与缓存 |
| 本地 IDE 配置（`.idea/`、`.vscode/`） | 已在 `.gitignore` 中 |
| 大文件、临时脚本、调试代码 | 拖慢仓库、污染历史 |
| 无意义的提交信息（`wip`、`修改`、`更新`、`111`） | 会原样出现在 Release 日志里 |

## 5. 分支与合并策略

| 分支 | 用途 |
| --- | --- |
| `main` | 主干，**每次 push 都会触发 Gitee Go 构建并发布 Release** |
| `dev` | 开发集成分支 |
| `feature/*`、`fix/*` | 功能/修复分支 |

要求：

1. **`main` 上推送即发布**，推送前确认 `VERSION` 与测试状态
2. 合并到 `main` 建议用 **squash merge**，一个 PR 在 `main` 上只留一条提交
3. squash 后的**合并标题也必须符合本规范**——它会直接成为 Release 日志的一行
4. 合并提交若写成 `Merge branch 'dev' into main`，会落入「📌 其他变更」

> 历史教训：仓库里曾出现 `update VERSION.`、`新增发布者名称和发布者网站或联系方式` 这类无 type 的提交，全部落入了「其他变更」。

## 6. 版本号与 CHANGELOG 联动

- **`VERSION` 文件是应用版本号的唯一真实来源**，不要直接改 `manifest` 或 `app/config.py`（打包时由 `scripts/sync_version.py` 自动同步）
- 版本号遵循语义化版本 `MAJOR.MINOR.PATCH`：

| 改动类型 | 版本位 |
| --- | --- |
| 破坏性变更 | `MAJOR` |
| `feat` 新功能 | `MINOR` |
| `fix` / `security` / `perf` | `PATCH` |
| `refactor` / `test` / `ci` / `docs` / `chore` | 一般不发版，累积到下个版本 |

发版流程：

```bash
# 1. 更新版本号的唯一来源
echo "0.8.0" > VERSION

# 2. 自动生成/更新 CHANGELOG（同版本重复运行幂等）
python3 scripts/gen_release_notes.py --update-changelog

# 3. 提交并发到 main，流水线自动构建发布
git add VERSION CHANGELOG.md
git commit -m "chore(release): 发布 0.8.0"
git push origin main
```

本地预览待发布的日志：

```bash
python3 scripts/gen_release_notes.py              # 打印到终端
python3 scripts/gen_release_notes.py --from v0.7.0 # 指定范围
```

## 7. 提交前检查清单

- [ ] 标题符合 `<type>(<scope>): <subject>` 格式
- [ ] `type` 在 2.2 表格内，能准确反映改动性质
- [ ] 涉及破坏性变更时加了 `!`
- [ ] 一个提交只做一件事，没有混入无关改动
- [ ] 没有提交密钥、构建产物、依赖目录
- [ ] 测试通过（`bash scripts/run_tests.sh`）
- [ ] 静态检查通过（`ruff check app cmd scripts tests --select F,E9`）
- [ ] 涉及功能/修复时，已确认是否需要同步更新 `VERSION`

## 8. 正反示例

### ✅ 推荐

```
feat(import): 差异报告支持导出 CSV
fix(security): 账单目录绝对路径不再回传给前端
fix(concurrency): 迁移与备份恢复统一互斥锁
feat(core): 中间件实现集中式权限控制
perf(ci): 国内加速 Python 环境部署
refactor(api): 请求级会话复用与身份依赖别名
test: 固化 app/static 构建产物约定
docs(ci): 明确 Node 版本策略
chore(deps): Node 版本从 20.19.0 升级到 24.18.0
revert(ci): 回退 VERSION 变更触发，改为总是构建
feat(api)!: 统一错误响应结构，移除旧版 code 字段
```

### ❌ 不推荐（会落入「📌 其他变更」或信息不足）

```
update VERSION.                          # 无 type，建议 chore(release): 更新版本号至 0.7.0
新增发布者名称和发布者网站或联系方式        # 无 type，建议 docs(manifest): 补充发布者信息
修改了几个文件                            # 无 type 且无信息量
wip                                      # 禁止
Merge branch 'dev' into main              # 建议 squash 后重写标题
feat(v0.5): 导入差异报告导出 CSV           # scope 不应是版本号
修复bug                                   # 无 type 且描述模糊
```

## 9. 可选工具配置

### 9.1 提交模板

仓库提供 `.gitmessage` 模板，配置后 `git commit` 会自动带出格式提示：

```bash
git config commit.template .gitmessage
# 全局生效
git config --global commit.template /path/to/fn-finstat/.gitmessage
```

### 9.2 commit-msg 校验钩子（可选）

把下面内容保存为 `.githooks/commit-msg` 并 `chmod +x`，启用后会在提交时校验格式：

```bash
git config core.hooksPath .githooks
```

```bash
#!/bin/bash
# 校验提交信息符合约定式提交格式（不符合仅警告，不阻断）
MSG_FILE="$1"
SUBJECT="$(head -1 "$MSG_FILE")"

PATTERN='^(security|feat|fix|perf|refactor|test|ci|build|docs|style|chore|revert)(\([^)]+\))?!?[[:space:]]*[:：][[:space:]]*.+$'

if [[ ! "$SUBJECT" =~ $PATTERN ]]; then
  echo "⚠️  提交标题不符合约定式提交格式："
  echo "    $SUBJECT"
  echo ""
  echo "    正确格式：<type>(<scope>): <subject>"
  echo "    示例：feat(import): 差异报告支持导出 CSV"
  echo "    详见 docs/开发规范/Git提交规范.md"
  echo ""
  echo "    如需强制提交，请使用 git commit --no-verify"
  exit 1
fi

if [ ${#SUBJECT} -gt 72 ]; then
  echo "⚠️  提交标题超过 72 字符（当前 ${#SUBJECT}），建议精简"
fi
```

> 钩子默认不启用——团队若要强制规范，再统一 `git config core.hooksPath .githooks` 并纳入仓库管理。
