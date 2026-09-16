# 更新日志

本文件由 `scripts/gen_release_notes.py` 自动生成，请勿手工编辑已发布版本的内容。
提交信息请遵循[约定式提交](https://www.conventionalcommits.org/zh-hans/)。

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
