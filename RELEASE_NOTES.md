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
