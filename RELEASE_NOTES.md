## fn-finstat v1.1.2

> 🧪 **测试版本**：由 dev 分支自动构建，仅供验证使用，请勿作为正式版本分发。

> 📅 发布日期：2026-09-29 · 🔢 提交数量：14 · 👥 贡献者：kafei、zhangyilin_233

### ✨ 新功能

- **ai**: AI-4 报告主动推送（月度自动生成，默认开可关） (fda6bc3)
- **forecast**: AI-5 订阅侦探（时间线/台阶涨价/僵尸订阅，零 AI 成本） (7cf7b21)
- **mcp**: AI-7 工具集扩展（预测/健康/支出结构/借贷，4 → 8 个只读工具） (786dc29)
- **stat**: AI-2 记账体检向导（数据质量检查 + 一键跳转筛选，零 AI 成本） (c7d55cc)
- **search**: AI-8 备注语义检索（零依赖 TF-IDF，MCP 工具 8→9） (93baf27)
- **settings**: 设置页分组导航改版（4 业务域分组 + 吸顶 scrollspy） (d4cb244)
- **forecast**: AI-9 What-if 反事实模拟（分类月均线性外推 + 储蓄目标联动，零 AI 成本） (8d923d9)
- **ai**: AI-6 场景化预算模板 + AI-10 家庭月度 AI 复盘（脑洞清单收口） (65c7763)

### 🐛 问题修复

- **coach**: 修复 CoachCard 两处存量缺陷（dev 构建打开即整页白屏） (f922def)
- **ci**: 清理三处未使用导入，修复 ruff 门禁拦截构建 (48fa7e6)

### 📝 文档

- **changelog**: 更新变更日志与发布说明 (bd63662)

### 🔧 杂项维护

- **notify**: 清理 send_webhook 中不可达的重复代码块 (fa0c597)
- **release**: 版本推进到 1.1.2,更新变更日志与发布说明 (4c1ba73)

### 📌 其他变更

- dev 版本号递增至 1.1.1 (4f06a7a)

---

**安装**：在飞牛 OS 应用中心手动安装本 Release 的 fpk 附件 —— 正式版为 `fn-finstat-latest.fpk`、测试版为 `fn-finstat-dev.fpk`，`fn-finstat-v1.1.2.fpk` 为本次构建的带版本号副本。
**校验（MD5）**：下载附件 `MD5SUMS.txt`，与 fpk 放在同一目录后执行 `md5sum -c MD5SUMS.txt`（macOS 用 `md5 -c MD5SUMS.txt`）。
**变更范围**：6231d2c9620cc9dc5b72b2cf2c3dddc388e5af66..HEAD
