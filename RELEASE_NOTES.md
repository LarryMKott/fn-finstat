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
