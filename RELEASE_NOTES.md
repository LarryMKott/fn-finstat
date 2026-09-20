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
