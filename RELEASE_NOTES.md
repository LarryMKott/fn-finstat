## fn-finstat v1.1.1

> 📅 发布日期：2026-09-26 · 🔢 提交数量：18 · 👥 贡献者：zhangyilin_233

### ✨ 新功能

- **db**: schema v16 分类扩展——category_keywords 表 + 内置词播种 + categories 层级三列 (49d30bb)
- **category**: 关键词服务与 CRUD 接口,匹配链改为读关键词表 (5484f94)
- **ai**: 分类关键词/子类 AI 生成两段式 + CAP-3 自动建分类 + 自动化三开关 (988405c)
- **backup**: 备份 v7 分类层级与关键词扩舱,跨库搬移按名重映射 (48aabea)
- **frontend**: 分类页树形化 + 关键词抽屉 + AI 候选弹窗 + 设置页自动化开关 (c1ff595)

### 🐛 问题修复

- **build**: 修复本地打包在版本解析步必然失败 (edc6f37)
- **security**: 修复 OWASP 专项测试确认的 P0/P1 缺陷 (a4f7ca7)
- **ai**: 归类解析失败零成本抢救 + 空 content 显式报错 (6231d2c)

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
- **release**: 版本推进到 1.1.1,更新变更日志与发布说明 (e759945)

---

**安装**：在飞牛 OS 应用中心手动安装本 Release 的 fpk 附件 —— 正式版为 `fn-finstat-latest.fpk`、测试版为 `fn-finstat-dev.fpk`，`fn-finstat-v1.1.1.fpk` 为本次构建的带版本号副本。
**校验（MD5）**：下载附件 `MD5SUMS.txt`，与 fpk 放在同一目录后执行 `md5sum -c MD5SUMS.txt`（macOS 用 `md5 -c MD5SUMS.txt`）。
**变更范围**：57bc54f58d2b72936aba26b02fc0d4212bef91bd..HEAD
