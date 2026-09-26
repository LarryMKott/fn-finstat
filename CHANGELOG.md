# 更新日志

本文件由 `scripts/gen_release_notes.py` 自动生成，请勿手工编辑已发布版本的内容。
提交信息请遵循[约定式提交](https://www.conventionalcommits.org/zh-hans/)。

## fn-finstat v1.1.1

> 📅 发布日期：2026-09-26 · 🔢 提交数量：16 · 👥 贡献者：zhangyilin_233

### ✨ 新功能

- **db**: schema v16 分类扩展——category_keywords 表 + 内置词播种 + categories 层级三列 (49d30bb)
- **category**: 关键词服务与 CRUD 接口,匹配链改为读关键词表 (5484f94)
- **ai**: 分类关键词/子类 AI 生成两段式 + CAP-3 自动建分类 + 自动化三开关 (988405c)
- **backup**: 备份 v7 分类层级与关键词扩舱,跨库搬移按名重映射 (48aabea)
- **frontend**: 分类页树形化 + 关键词抽屉 + AI 候选弹窗 + 设置页自动化开关 (c1ff595)

### 🐛 问题修复

- **build**: 修复本地打包在版本解析步必然失败 (edc6f37)
- **security**: 修复 OWASP 专项测试确认的 P0/P1 缺陷 (a4f7ca7)

### 🧪 测试

- 本地服务器用例就地绕开代理，消除全量跑的假失败 (70fb6af)
- 修复 CI 首跑暴露的 4 个环境依赖假失败 (c6dd352)
- 分类扩展全链路测试并同步夹具播种 (680b889)

### 👷 构建与流水线

- 新增 GitHub Actions 编译发布流水线（与 Gitee Go 同源） (dd06d84)

### 📝 文档

- 生成 v1.1.0 变更日志与发布说明 (5dcd612)
- 索引更新日期对齐实际改动，新增日期漂移核对脚本 (3426f2f)
- 分类扩展实现记录,方案转已实现,索引同步 (017d59a)

### 🎨 样式调整

- 按 CI 门禁补跑 black 全量格式化 (169e9c5)

### 🔧 杂项维护

- **release**: 应用描述改为按亮点分条的详细说明 (81cc9a1)

---

**安装**：在飞牛 OS 应用中心手动安装本 Release 的 fpk 附件 —— 正式版为 `fn-finstat-latest.fpk`、测试版为 `fn-finstat-dev.fpk`，`fn-finstat-v1.1.1.fpk` 为本次构建的带版本号副本。
**校验（MD5）**：下载附件 `MD5SUMS.txt`，与 fpk 放在同一目录后执行 `md5sum -c MD5SUMS.txt`（macOS 用 `md5 -c MD5SUMS.txt`）。
**变更范围**：57bc54f58d2b72936aba26b02fc0d4212bef91bd..HEAD

<!-- release-baseline: 017d59aefae1ee0b3ea80a6731834f0a6b8158b5 -->
<!-- release-start: 57bc54f58d2b72936aba26b02fc0d4212bef91bd -->

## fn-finstat v1.1.0

> 📅 发布日期：2026-09-21 · 🔢 提交数量：11 · 👥 贡献者：kafei、zhangyilin_233

### ✨ 新功能

- **ai**: 异常自检通知（AI-1）+ 教练上下文增强（AI-3） (eef2aa9)
- **ai**: AI 接入支持多供应商（OpenAI 兼容协议） (7f5efe5)
- **ai**: DeepSeek 预置模型新增 deepseek-flash (29949f7)

### 🐛 问题修复

- **ai**: 模型名支持手动输入（input+datalist 替代锁死的 select） (b430bf1)

### 📝 文档

- 文档体系全量同步至 1.1.0 现状 (508bc99)
- 归档交互式架构图与导入流程图到 docs/diagrams (76551e9)

### 🎨 样式调整

- 移除 verify_note_search.py 未使用的 os 导入 (b42fe45)
- verify_note_search.py black 格式化 (e5279c5)

### 🔧 杂项维护

- **release**: 更新应用中心展示的更新说明至 1.1.0 (57bc54f)

### 📌 其他变更

- 更新版本号至 1.0.0 (6bb2443)
- dev 版本号递增至 1.1.0 (b15b87c)

---

**安装**：在飞牛 OS 应用中心手动安装本 Release 的 fpk 附件 —— 正式版为 `fn-finstat-latest.fpk`、测试版为 `fn-finstat-dev.fpk`，`fn-finstat-v1.1.0.fpk` 为本次构建的带版本号副本。
**校验（MD5）**：下载附件 `MD5SUMS.txt`，与 fpk 放在同一目录后执行 `md5sum -c MD5SUMS.txt`（macOS 用 `md5 -c MD5SUMS.txt`）。
**变更范围**：a11c827d40c7ca3f0dd79d6fad6dd9213f77b8f6..HEAD

<!-- release-baseline: 57bc54f58d2b72936aba26b02fc0d4212bef91bd -->
<!-- release-start: a11c827d40c7ca3f0dd79d6fad6dd9213f77b8f6 -->

## fn-finstat v1.0.0

> 📅 发布日期：2026-09-20 · 🔢 提交数量：19 · 👥 贡献者：kafei、zhangyilin_233

### ✨ 新功能

- **frontend**: 新增 5 套配色主题，与日间/夜间正交可组合 (e43ae7a)
- **frontend**: 新增「极光」科技感主题——深空底+量子青霓虹+HUD 细节层 (e0b2eed)
- **mcp**: MCP Server（T-1.1）——只读工具集，零新依赖 JSON-RPC 2.0 (bd4020d)
- **coach**: AI 财务教练（T-1.6）——结构化摘要 + DeepSeek 对话，不外传原始流水 (9f4eb70)
- **security**: 隐私加密 + 异地备份（T-1.7）——v1.0 收口 (a11c827)

### 🐛 问题修复

- **tests**: 修正配置权限用例的 monkeypatch 失效，CI 不再误报 (9f87624)
- **frontend**: 清理健康评分重构残留的未使用变量 (ecd13d2)
- **build**: 打包通配 app 顶层模块，修复 file_settings.py 漏打包 (571646b)
- **data**: 借贷/报销/储蓄四表接入换库与备份恢复全链路（审查 P0/P1） (caa3a1e)
- **security**: 权限中间件内验证 API Token，堵住垃圾 Token 绕过 401（审查 P1） (6a0a011)
- **services**: 审查 P1/P2 后端收口——家庭生命周期/日期校验/报销守卫/审计打点 (486ef99)
- **frontend**: 审查 P0/P1 前端收口——渲染崩溃/竞态/时区/交互 (ad97977)

### ♻️ 代码重构

- **frontend**: style.css 按主题域拆分到 styles/ 目录 (4856c65)

### 🧪 测试

- 修复配置权限用例的路径绑定导致的 CI 失败 (10f24d9)

### 📝 文档

- **changelog**: 更新变更日志与发布说明（v0.7.4） (22d79d8)

### 🎨 样式调整

- black 格式化 API Token 相关四个文件 (feb55f1)
- mcp_service / test_mcp 清理未使用导入 (9db44f9)

### 🔧 杂项维护

- **frontend**: 同步 package-lock.json 内嵌版本号至 0.7.4 (6604642)

### 📌 其他变更

- 更新版本号至 0.7.5 (0c57f9f)

---

**安装**：在飞牛 OS 应用中心手动安装本 Release 的 fpk 附件 —— 正式版为 `fn-finstat-latest.fpk`、测试版为 `fn-finstat-dev.fpk`，`fn-finstat-v1.0.0.fpk` 为本次构建的带版本号副本。
**校验（MD5）**：下载附件 `MD5SUMS.txt`，与 fpk 放在同一目录后执行 `md5sum -c MD5SUMS.txt`（macOS 用 `md5 -c MD5SUMS.txt`）。
**变更范围**：ff9b357b79ae980d601a10922138c95581fcfe7d..HEAD

<!-- release-baseline: a11c827d40c7ca3f0dd79d6fad6dd9213f77b8f6 -->
<!-- release-start: ff9b357b79ae980d601a10922138c95581fcfe7d -->

## fn-finstat v0.7.4

> 📅 发布日期：2026-09-19 · 🔢 提交数量：45 · 👥 贡献者：kafei、zhangyilin_233

### 🔒 安全修复

- 收紧配置与数据文件的落盘权限，Webhook 错误信息脱敏 (066cbbf)
- 修复重定向禁用无效致 Key 可泄漏，落地三项审计加固 (5e6e48e)

### ✨ 新功能

- **forecast**: 新增预算建议与现金流预测（T-6.4） (769fc70)
- **query**: 新增对话式查账界面与追问上下文（T-6.2） (1c0c7dc)
- **ai**: 报告附录数字可点击追溯来源流水（T-6.5 联动收口） (6b0a0ce)
- **db**: 新增账本维度模型与 schema v8 迁移 (31532c0)
- **api**: 新增账本接口并打通读写两侧账本解析 (0265b47)
- **build**: 迁移自检新增账本维度升级场景 (d81dcc7)
- **family**: 新增家庭空间与合并视图，账本切换与管理界面（T-7.2） (67eaa87)
- **budget**: 预算域基础与 T-7.1 遗留收口——账本移动、账本口径、schema v10 (2bc5883)
- **budget**: 家庭预算（T-7.3）——管理员设定、全员进度、解散联动、备份贯通 (743a03e)
- **reimb**: 报销 / 垫付工作流（T-7.4）——报销单实体、状态机、存量迁移 (cf262bd)
- **loans**: 借贷台账（T-7.5）——借出借入、还款记录、还清即结项 (ed75fd6)
- **audit**: 操作审计（T-7.6）——业务写操作留痕，v0.7 收口 (4851c85)
- **analysis**: v1.0 第 1 批——财务健康评分（T-1.3）与储蓄目标（T-1.4） (f1493cc)
- **analysis**: 支出结构拆分（T-1.5）——固定支出 vs 弹性支出（必选项 / 可砍项） (1a25a3e)
- **api**: 开放 API Token（T-1.2）——只读凭证、只存哈希 (a262c15)
- **api**: 身份解析落地 get_identity，网关头优先 + Token 兜底 (65da017)

### 🐛 问题修复

- **ledger**: 收口备份恢复默认账本唯一性并透出删除并入计数（评审修复） (124bfb0)
- **import**: 统一账单金额文本解析，修复微信脏数据致 500 的隐患 (b3ecc2f)
- **category**: 创建分类仅将唯一约束冲突视为重复 (387e76f)
- **core**: Token 分支仅在无网关身份时生效，避免普通写请求被误拒 (d9f8f8e)

### ⚡ 性能优化

- **db**: 账单分类回写分组批量 UPDATE 等四处查询优化 (f475c7f)
- **stat**: 消费地图地域推断按入参文本缓存 (a76b2e3)

### ♻️ 代码重构

- **tests**: 测试按 app 分层归入子目录，便于人工维护 (5116abb)
- **core**: 领域常量单一来源化，合并三份重复工具逻辑 (01e29da)
- **api**: 共享查询参数 Annotated 别名，收敛重复 Query 定义 (b07dbaa)
- 配置按「启动静态 / 运行期文件」拆分为两块 (a65671b)
- **core**: 请求 ID 存放键与安全响应头下沉 context 模块 (7c2e620)

### 🧪 测试

- 补充账本维度迁移与隔离用例 (71a1bbe)
- **budget**: schema v10 迁移用例——幂等、旧行保留、迁移后 DAO 全流程 (47b5980)
- **api**: 补 Token 全路由权限面覆盖，含只读/写方法/管理面三层断言 (02d521d)

### 📝 文档

- **changelog**: 更新变更日志与发布说明 (809579f)
- **manifest**: 更新应用中心更新说明至 0.7.3 (08b8e6f)
- **spec**: 同步 app/static 产物归属约定 (1ee1ed1)
- 同步 T-7.1 账本维度实现与文档索引 (4ea702e)
- devlog 补记收尾两笔与逐快照验证方式 (e8397a8)
- 家庭预算（T-7.3）实现记录与计划文档销项 (8d7dcde)
- 补 T-1.2 开放 API Token 实现记录与索引 (79e07ea)
- **devlog**: 记录提交历史中间点不可运行及不改写历史的决定 (a496a27)

### 🔧 杂项维护

- **release**: 更新版本号至 0.7.4 (c4bfafc)
- app/static 整目录不再入库，全部由前端构建生成 (946331a)
- 格式化 forecast/nl_query 相关文件以通过 CI black 门禁 (c0a1b2c)
- **assets**: 根目录图标归入 assets/icons/，清理打包构建残留 (336c29c)
- **ci**: 手动触发测试版流水线 (ff9b357)

---

**安装**：在飞牛 OS 应用中心手动安装本 Release 的 fpk 附件 —— 正式版为 `fn-finstat-latest.fpk`、测试版为 `fn-finstat-dev.fpk`，`fn-finstat-v0.7.4.fpk` 为本次构建的带版本号副本。
**校验（MD5）**：下载附件 `MD5SUMS.txt`，与 fpk 放在同一目录后执行 `md5sum -c MD5SUMS.txt`（macOS 用 `md5 -c MD5SUMS.txt`）。
**变更范围**：7810dc9741974811ca9b6af1d6fdadc025dae018..HEAD

<!-- release-baseline: ff9b357b79ae980d601a10922138c95581fcfe7d -->
<!-- release-start: 7810dc9741974811ca9b6af1d6fdadc025dae018 -->

## fn-finstat v0.7.3

> 📅 发布日期：2026-09-18 · 🔢 提交数量：17 · 👥 贡献者：kafei、zhangyilin_233

### ✨ 新功能

- **automation**: 通知中心交付事件通知与出站 Webhook（T-5.4） (9d61bdf)
- **ai**: 新增一句话查账意图翻译层（T-6.1） (9f8649b)
- **category**: 分类规则自学习，归类优先级落地为已学习规则优先（T-6.3） (eb01922)
- **build**: 新增 dev 分支测试版打包渠道 (a5726e6)
- **update**: 新增应用检查更新功能（接口与关于卡片） (ecd520d)

### 🐛 问题修复

- **frontend**: 移除 NotificationCard 未使用的 toast 导入 (8c2979a)
- **update**: 更新说明按基版本匹配，CHANGELOG 落后时不再错标 (18c0546)
- **ci**: Release 描述改用只含本次版本的 RELEASE_NOTES.md (3c34753)

### 📦 打包构建

- 发布 0.7.1 (337ea01)

### 📝 文档

- **spec**: 补充构建渠道规范与测试版发布流程 (334e317)
- **manifest**: 更新应用介绍与项目简介 (f280008)
- **spec**: 同步检查更新到架构方案与发布流程 (afe6deb)
- **spec**: 修正索引里的版本号维护约定 (4cc0a6f)
- **changelog**: 补齐 CHANGELOG 至 0.7.3 并约束描述来源 (4db17c3)
- **spec**: 同步发布描述改为 RELEASE_NOTES.md 的流程约束 (7810dc9)

### 🔧 杂项维护

- **release**: 更新版本号至 0.7.2 (6e5ffc1)
- **release**: 更新版本号至 0.7.3 (d066ce8)

---

**安装**：在飞牛 OS 应用中心手动安装本 Release 的 fpk 附件 —— 正式版为 `fn-finstat-latest.fpk`、测试版为 `fn-finstat-dev.fpk`，`fn-finstat-v0.7.3.fpk` 为本次构建的带版本号副本。
**校验（MD5）**：下载附件 `MD5SUMS.txt`，与 fpk 放在同一目录后执行 `md5sum -c MD5SUMS.txt`（macOS 用 `md5 -c MD5SUMS.txt`）。
**变更范围**：75ca04686ebc3460e1102ce910d0b98bc67663f3..HEAD

<!-- release-baseline: 7810dc9741974811ca9b6af1d6fdadc025dae018 -->
<!-- release-start: 75ca04686ebc3460e1102ce910d0b98bc67663f3 -->
## fn-finstat v0.7.1

> 📅 发布日期：2026-09-17 · 🔢 提交数量：26 · 👥 贡献者：kafei、zhangyilin_233

### 🔒 安全修复

- **auth**: 网关模式无头请求改判 401 并新增独立部署来源校验 (b943fc0)

### ✨ 新功能

- **build**: 流水线自动整理 Release 日志 (e74630a)

### 🐛 问题修复

- **ci**: 补全质量门禁并修复发布链路三处缺陷 (3a67ce8)
- **ci**: Release tag 改用语义化版本号 (11bbb80)
- **frontend**: 升级 @vitejs/plugin-vue 至 ^6.0.9，修复 npm ci 门禁依赖解析冲突 (7697c94)
- **scripts**: fix_fpk_perm 规范化路径并拒绝穿越分量 (32f8ad5)
- **build**: 生命周期脚本补环境变量守卫并收紧向导校验 (4425c84)
- **build**: 修正 sync_version.py 打包模式参数处理 (5e9ec71)
- **build**: Windows 脚本支持 PYTHON 覆盖并修复 bat shim 调用链 (a66d38c)
- **ci**: 修复 Release 描述未渲染 releaseNode.txt 的问题 (b23a61d)
- **ci**: Release 描述改用已入库的 CHANGELOG.md (75ca046)

### ♻️ 代码重构

- **frontend**: 移除未使用的导出与死代码 (959cfad)
- **api**: schemas 包不再聚合再导出 (495f48d)

### 🧪 测试

- 统计接口测试重构归位并清理被 pytest 取代的验证脚本 (7356c38)

### 📝 文档

- **spec**: 新增提交、前端、后端、构建四份开发规范 (b800e99)
- 新增发布流程文档与初始 CHANGELOG (e20f0bd)
- **spec**: 按评审结论修订四份开发规范 (de65e46)
- **spec**: 精简四份开发规范，面向 AI 消费优化 (70de6ab)
- **spec**: 新增前后端架构设计方案并更新文档索引 (abedc41)
- **spec**: 新增前后端架构设计方案评审报告 (b94f929)
- **devlog**: 归档 AI 报告方案与 v0.5 收口两份 Trae 过程文档 (0bd23a9)
- 同步信任边界说明、冒烟测试指引与开发日志状态 (20a4553)

### 🎨 样式调整

- 应用 black 统一代码格式 (8326c20)

### 🔧 杂项维护

- gitignore 排除 Trae IDE 本地目录 (3c618c2)
- **build**: 同步合并后源码的本地构建产物 (4d4fa04)
- 移除 wizard 目录占位 .gitkeep (32b7f34)

---

**安装**：下载附件 `fn-finstat-v0.7.1.fpk`，在飞牛 OS 应用中心手动安装。
**变更范围**：246d5d00f120b418eb255e4898ce6db9268941ff..HEAD

<!-- release-baseline: 75ca04686ebc3460e1102ce910d0b98bc67663f3 -->

## fn-finstat v0.7.0

> 📅 发布日期：2026-09-15 · 🔢 提交数量：30 · 👥 贡献者：kafei、zhangyilin_233

### 🔒 安全修复

- **ai**: AI 请求禁用重定向跟随 + 打包后清理暂存目录 (17f0d2e)

### ✨ 新功能

- **core**: 中间件实现集中式权限控制（管理面策略表 + 路由守卫双层防御） (2a73612)
- **build**: 引入 VERSION 作为版本号唯一真实来源，CI tag 改用应用版本号 (f12443a)
- **ci**: 仅当 VERSION 文件变更时触发构建发布 (c2244cd)
- **v0.5**: 导入差异报告导出 CSV 与 AI 报告数据来源附录，发布 0.7.0 (246d5d0)

### 🐛 问题修复

- **security**: 账单目录绝对路径不再回传给前端 (3dd39d7)
- **import**: 解析异常回显前脱敏内部路径 (78085aa)
- **ci**: 修复流水线 stages 结构回归（stage 属性直接写在列表项上） (412af44)
- **ci**: 修复 npm ci peer 冲突与构建日志版本读取，本地全流程模拟通过 (dd7ce6f)
- **ci**: 修复全新 clone 上 import app.main 因 assets 目录缺失而崩溃 (f3bd051)
- **test**: 修复目录监听测试在 Linux 上的换行符误判 (0d04a2d)
- **ci**: stage 显式声明 strategy/trigger，修复发行版未创建 (5194bec)
- **ci**: release stage 内重新读取 VERSION，修复 APP_VERSION 空值 (2f9f844)
- **ci**: 文件名带版本号，release@gitee 用构建号 tag (395cd64)
- **ci**: 每个 step 独立 container，test/build step 补 ci_env.sh (83d9f35)

### ⚡ 性能优化

- **ci**: 国内加速 Python 环境部署 (641cc8d)

### ♻️ 代码重构

- **api**: 请求级会话复用、身份依赖别名与 HTTP 观测中间件 (49ff3e6)
- **ci**: 整理构建发布流程，对齐官方文档与参考配置 (b961a50)
- **ci**: 构建阶段拆分为 3 个串行 step (f55d9ea)
- **ci**: 区分测试依赖和打包依赖 (5bed63f)
- **ci**: 合并回单脚本 ci_build.sh，build stage 用单 step (1b5b3d7)

### 🧪 测试

- 固化 app/static 构建产物约定（产物不入库、测试不依赖、内容可再生） (92b0b69)

### 📝 文档

- 新增 dev 分支全量代码审查报告并同步文档索引 (5fd2d40)
- **ci**: 明确 Node 版本策略——优先使用全局 PATH 安装（当前 24.21.0） (da81c59)

### 🔧 杂项维护

- **lint**: 清理未使用的导入与局部变量 (87c1601)
- **ci**: 收紧构建门禁（npm ci / fnpack 退出码 / 产物校验 / ruff） (4f8097d)
- **db**: 提取共享 IN 分片助手，收敛私有 _STATE 导出；清理本地临时文件 (61eb4c9)
- **ci**: Node 版本从 20.19.0 升级到 24.18.0 (9f6da4e)

### ⏪ 版本回退

- **ci**: 回退 VERSION 变更触发，改为总是构建 (36e9db6)

### 📌 其他变更

- update VERSION. (f836905)

---

**安装**：下载附件 `fn-finstat-v0.7.0.fpk`，在飞牛 OS 应用中心手动安装。
**变更范围**：最近 30 个提交

<!-- release-baseline: 246d5d00f120b418eb255e4898ce6db9268941ff -->
