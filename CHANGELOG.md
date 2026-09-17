# 更新日志

本文件由 `scripts/gen_release_notes.py` 自动生成，请勿手工编辑已发布版本的内容。
提交信息请遵循[约定式提交](https://www.conventionalcommits.org/zh-hans/)。

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
