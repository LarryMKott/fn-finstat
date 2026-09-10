# fn-finstat 财务统计

> 基于 FastAPI + SQLite，用于导入微信/支付宝账单，实现个人收支统计可视化，适配飞牛OS（fnOS）应用开放平台规范。
>
> 开发文档参考：https://github.com/LarryMKott/fnnas-docs

## ✨ 功能简介

- **账单导入**：支持微信支付 xlsx 账单、支付宝 csv 账单自动解析
- **多账号数据隔离**：账单按登录的飞牛账号区分（统一网关转发 `X-Trim-Userid` 可信头），各账号只看到自己的流水与统计；升级前历史数据可在设置页一键认领
- **数据清洗**：自动识别收支类型，交易号唯一去重，过滤转账类流水
- **分类管理**：预置消费分类，支持自定义新增分类，内置关键词自动归类
- **流水管理**：手动新增、编辑、删除账单记录，支持多条件分页筛选
- **统计看板**：收支汇总、月度趋势、分类支出饼图、商户消费 TOP 排行
- **主题切换**：顶栏一键循环切换 跟随系统 / 浅色 / 深色，auto 模式随系统主题实时联动，偏好本地记忆、首屏无闪烁，图表配色同步切换
- **多类型数据库**：安装向导可选 SQLite（默认）/ MySQL / PostgreSQL，连接参数随向导收集；ORM 模型（SQLAlchemy）统一三方言
- **设置页迁移数据库**：「设置」页可随时把现有数据一键迁移到新的 MySQL / PostgreSQL 数据库并立即切换，原库保留可回退，无需重装重启
- **服务端口可自定义**：向导可配置 HTTP 服务端口（`wizard_port`，默认 8090），避免与其它程序端口冲突；fnOS 统一网关模式经 Unix Socket 通信，不占用 TCP 端口
- **卸载可选清除数据**：卸载时可选保留数据（默认，重装续用）或彻底清除本地账单数据；外部数据库数据不受影响
- **接口地址可配置**：前后端接口地址前缀随向导设置（前端运行时自动适配，改前缀无需重新构建）
- **存储**：SQLite 单文件数据库或外部数据库；飞牛OS 环境使用 `TRIM_PKGVAR` 持久目录，升级/卸载保留用户数据

## 📁 项目结构

```
fn-finstat/
├── manifest                  # FPK 应用清单（fnOS 官方规范，INI 格式）
├── ICON.PNG / ICON_256.PNG   # 打包图标（fnpack build 检查项）
├── config/
│   ├── privilege             # 运行用户（专用应用用户）
│   └── resource              # 资源声明
├── wizard/                   # 安装/配置向导（数据库类型与连接参数、接口地址前缀）
├── cmd/                      # FPK 生命周期脚本
│   ├── main                  # 生命周期入口（start/stop/status）
│   ├── install_callback      # 安装回调，安装 Python 依赖
│   ├── upgrade_callback      # 升级回调，同步更新 Python 依赖
│   └── uninstall_callback    # 卸载回调（按卸载向导选项清除数据）
├── scripts/                  # 本地开发/构建辅助脚本
│   └── start.sh              # 本地开发启动脚本（生产环境由 cmd/main 负责）
├── app/                      # 主应用源码目录
│   ├── main.py               # FastAPI 入口
│   ├── config.py             # 配置，读取 fnOS 环境变量
│   ├── requirements.txt      # Python 依赖
│   ├── db/                   # 数据库层
│   │   ├── base.py           # SQLite 连接、表初始化
│   │   └── dao/              # 数据访问层 DAO（bill/category/stat）
│   ├── services/             # 业务逻辑层
│   ├── parsers/              # 账单文件解析器（微信/支付宝，抽象基类）
│   ├── api/                  # FastAPI 路由接口
│   ├── schemas/              # Pydantic 请求/响应模型
│   ├── utils/                # 通用工具（上传、筛选、关键词归类）
│   ├── ui/                   # fnOS 桌面入口配置与图标
│   └── static/               # 前端构建产物（由 frontend/ 构建生成，请勿手改）
├── scripts/                  # 开发工具（样例账单生成、图标生成）
├── frontend/                 # 前端源码（Vue 3 SFC + Vite 工程）
│   ├── src/                  #   组件、路由状态与样式源码
│   └── vite.config.js        #   构建配置（产物直接输出至 app/static）
├── .env.dev                  # 本地开发环境变量（不打包进 FPK）
└── docs/
    └── 项目需求文档.md
```

> **与早期草案的差异说明**：草案中的 `fnpack.json` 在 fnOS 官方规范中不存在——官方以 `manifest` 文件作为应用清单，且打包检查强制要求 `config/privilege`、`config/resource`、`ICON.PNG`、`ICON_256.PNG`、`wizard/` 等文件。本项目按官方规范落地：生命周期脚本采用官方命名 `cmd/main` / `cmd/install_callback` / `cmd/uninstall_callback`，本地开发启动脚本放 `scripts/start.sh`（不随包发布）；Python 运行时按官方规范声明 `install_dep_apps=python312` 并在脚本中自动加入 PATH。

## 🧰 环境依赖

- **本地开发**：Python >= 3.9（自备），系统依赖 `python3 python3-pip`
- **前端构建**：Node.js >= 18 + npm（仅修改 `frontend/` 前端源码后重新构建时需要）
- **飞牛OS 生产**：Python 运行时由平台提供，已在 `manifest` 声明 `install_dep_apps=python312`，生命周期脚本会自动将其加入 PATH；无需在 fnOS 手工安装 Python
- Python 包：`fastapi uvicorn[standard] openpyxl python-multipart pydantic>=2.0 python-dotenv`（安装脚本自动处理）

## 🚀 本地开发部署

> 本地开发模拟飞牛OS 环境变量，不影响 FPK 打包后的生产行为。

1. 创建虚拟环境（在项目根目录执行）：

```bash
python3 -m venv app/venv
source app/venv/bin/activate
pip install -r app/requirements.txt
```

2. 本地启动服务（从项目根目录启动，模块路径为 `app.main:app`）：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8090
# 或使用启动脚本：bash scripts/start.sh
```

3. 访问地址：

- 主页：http://127.0.0.1:8090/app/fn-finstat/（与生产统一网关同前缀；访问 http://127.0.0.1:8090/ 亦可）
- 接口文档：http://127.0.0.1:8090/docs

本地数据存放于项目根目录 `.local_data`，临时文件在 `.local_tmp`。

### 生成样例账单（可选）

```bash
python scripts/gen_sample_bills.py
```

生成微信/支付宝样例账单至 `.local_tmp/`，可在「账单导入」页面上传验证。

### 数据库与接口地址配置

安装向导（及应用设置中的配置向导）提供以下参数，修改后应用自动重启生效：

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `wizard_db_type` | 数据库类型：`sqlite` / `mysql` / `postgresql` | `sqlite` |
| `wizard_db_host` / `wizard_db_port` | 外部数据库地址与端口 | 127.0.0.1 / 3306 或 5432 |
| `wizard_db_name` / `wizard_db_user` / `wizard_db_password` | 数据库名与账号（需已创建并有读写权限） | fn_finstat |
| `wizard_api_base_path` | 前后端接口地址前缀，需与统一网关前缀一致（反代/独立部署可自定义） | `/app/fn-finstat` |
| `wizard_port` | HTTP 服务端口（独立部署/本地运行场景；fnOS 统一网关模式不占用 TCP 端口） | `8090` |

- 选 SQLite 时无需任何配置，数据写入 `TRIM_PKGVAR/finance/bill.db`，老版本数据自动沿用
- 选 MySQL / PostgreSQL 时，安装/升级/改配置回调会按需向 venv 安装对应驱动（PyMySQL / psycopg2-binary），应用启动时自动建表
- 本地开发可在 `.env.dev` 中用 `DB_TYPE`、`DB_HOST`、`API_BASE_PATH` 等同名变量模拟

### 设置页迁移数据库（免重装切换）

除安装向导外，应用「设置」页提供运行期数据库迁移（`/api/settings/*`）：

1. 填写目标 MySQL / PostgreSQL 连接信息 → **测试连接** 验证连通性（目标库为空/已有数据会提前告知）
2. **迁移并切换** → 建表并搬移全部流水与分类 → 立即切换到新数据库，全程无需重启
3. 连接信息持久化于 `TRIM_PKGVAR/finance/db_config.json`，重启后仍生效；原数据库只读保留，改回配置即可回退

细节规则：

- 目标库为空 → 整库搬移并保留流水编号；目标库已有数据 → 按交易单号/分类名去重合并
- 目标库驱动（PyMySQL / psycopg2-binary）缺失时自动安装，无需重装应用
- 向导显式配置的优先级高于设置页配置（改向导参数重启后以向导为准）
- 机制自检：`app/venv/Scripts/python.exe scripts/verify_db_migration.py`

### 升级与数据迁移

应用内置 schema 版本管理（`app/db/base.py`），升级安装后首次启动自动完成数据迁移，无需人工介入：

- **版本记录**：数据库内的 `app_meta` 表记录 `schema_version`。0.2.x 老库无此表，升级后按基线版本补记，历史数据原样保留
- **逐版本迁移**：新版本 schema 变更以迁移函数登记（`_MIGRATIONS`，每个函数负责 vN → vN+1），启动时按序应用；每个迁移独立事务并立即写版本戳，中断后重启自动从断点续迁
- **迁移前备份**：SQLite 在应用迁移前自动 checkpoint 并复制 `bill.db` 为 `bill.db.bak-v<N>`（与库文件同目录），迁移出问题可手动回退；外部数据库请依赖自身备份机制
- **缺少迁移实现时拒绝启动**并记录日志，宁可服务不可用也不静默跳过迁移损坏数据
- **切换数据库类型**（改向导配置）：类型变更记录于 `TRIM_PKGVAR/finance/db_meta.json`
  - 旧库为 SQLite → 新库为空时自动搬移全部流水与分类（旧 `bill.db` 文件保留不动，可随时切回）
  - 旧库为外部数据库 → 连接参数已失效，无法自动迁移；数据仍在原库中，日志会明确提示

开发者为未来版本添加迁移的模板见 `app/db/base.py` 中 `_MIGRATIONS` 注释；迁移机制由 `scripts/verify_migrations.py` 做场景化自检（全新安装 / 老库升级 / 模拟迁移 / 类型切换）。

### 账单按飞牛账号区分

通过飞牛统一网关访问时，网关在登录校验后转发可信身份头（`X-Trim-Userid` / `X-Trim-Username`），后端据此隔离数据：

- 账单的增删改查、账单导入、全部统计报表均只作用于当前登录账号的数据
- 消费分类为全局共享（各账号同一套分类，便于家庭场景统一管理分类口径）
- 本地开发、独立部署等无网关场景没有身份头，所有数据归入默认账号，行为与旧版一致
- ⚠️ 安全提示：多账号隔离依赖网关转发的可信身份头。独立部署时若把 HTTP 端口直接暴露给多人，`X-Trim-Userid` 可被客户端伪造冒充他人账号——多用户场景请务必通过飞牛统一网关访问，不要直接开放独立端口
- 升级前入库的历史流水（无归属）会迁移为默认账号数据；网关用户登录后可在「设置」页一键**认领到当前账号**

### 卸载与数据保留

卸载应用时会出现「数据清理」向导，由用户决定数据的去留：

- **默认（保留）**：账单数据库、数据库切换配置与应用日志保留在 `TRIM_PKGVAR` 持久目录，重新安装后自动沿用
- **勾选「删除全部本地数据」**：清除 `TRIM_PKGVAR/finance`（SQLite 数据库与切换配置）、应用日志与运行虚拟环境，删除后不可恢复
- 存放在外部 MySQL / PostgreSQL 中的数据不受卸载影响，需自行管理；向导页也会提示先完成迁移或备份

### 前端开发与构建

前端源码位于 `frontend/`（Vue 3 SFC + Vite），构建产物输出到 `app/static/`（FastAPI 托管目录，勿手改）：

```bash
cd frontend
npm install
npm run build    # 产物输出至 ../app/static/
```

开发调试可用 Vite 热更新（`/api` 已代理到本地 8090 后端）：

```bash
npm run dev      # http://127.0.0.1:5173
```

## 📦 打包安装到飞牛OS

1. **获取 fnpack 工具**：fnpack 是飞牛官方 CLI（各平台二进制：`fnpack-1.2.3-windows-amd64` / `linux-amd64` / `linux-arm64` / `darwin-amd64` / `darwin-arm64`），下载地址：`https://static2.fnnas.com/fnpack/fnpack-1.2.3-<平台>`（如 `fnpack-1.2.3-windows-amd64`）。Linux/macOS 可安装到系统路径：`chmod +x fnpack-* && sudo mv fnpack-* /usr/local/bin/fnpack`。

2. **本地一键打包**（推荐，Windows/Linux/macOS 通用）：

```bash
bash scripts/build_fpk.sh
# fnpack 不在 PATH 时：
FNPACK=/path/to/fnpack bash scripts/build_fpk.sh
```

脚本做三件事：组装干净暂存目录（只含打包必需文件，**排除** `app/venv`、`frontend/`、`.local_*`、`__pycache__`）→ `fnpack build` → 修正 Windows 打包丢失的 `cmd/` 可执行权限位（0666 → 0755）。

产物为项目根目录 **`fn-finstat.fpk`**（约 380KB，platform 声明为 all，无架构后缀）。

> 实测 fnpack 1.2.3 的校验比文档更严格：除文档列出的检查项外，还要求根目录存在 `LICENSE`、`cmd/` 下存在 `install_init` / `upgrade_init` / `uninstall_init` / `config_init` / `config_callback`（本项目均已内置）。

3. **安装测试**（任选其一）：
   - 飞牛OS 后台 → 应用中心 → 手动安装 FPK 包（适合本地验证）
   - 设备上使用 appcenter-cli：`appcenter-cli install-fpk fn-finstat.fpk`；项目在设备上时可直接 `appcenter-cli install-local` 快速验证
4. 安装完成后，桌面出现「财务统计」图标，点击经统一网关打开 Web 页面

生产环境：

- 访问方式：桌面「财务统计」图标 → 统一网关 `https://<nas>/app/fn-finstat`（复用系统域名，**经 NAS 登录态校验**，无独立端口）
- 网关 Socket：`${TRIM_APPDEST}/app.sock`（`cmd/main` 以 `uvicorn --uds` 监听）
- 持久化数据库：`${TRIM_PKGVAR}/finance/bill.db`
- 临时上传目录：`${TRIM_PKGTMP}/fn-finstat`
- 应用源码：平台将包内 `app.tgz` 解压到 `${TRIM_APPDEST}` 根目录（`requirements.txt`、`ui/` 在根，Python 包在 `app/` 子目录）；依赖安装在 `${TRIM_PKGVAR}/venv`，升级时由 `cmd/upgrade_callback` 同步更新

> 说明：统一网关只校验 NAS 登录态，业务层当前未做用户隔离——所有已登录用户共享同一套账本。

### 打包检查项（fnpack build 自动校验）

| 路径 | 说明 |
| --- | --- |
| `manifest` | 应用清单，含必要字段 |
| `config/privilege` | 运行用户，合法 JSON |
| `config/resource` | 资源声明，合法 JSON |
| `ICON.PNG` / `ICON_256.PNG` | 打包图标 |
| `app/`、`cmd/`、`wizard/` | 必选目录 |
| `app/ui/` | 声明 `desktop_uidir=ui` 时必须存在 |

## 📝 账单导出说明

**微信支付账单**

微信 → 我 → 服务 → 钱包 → 账单 → 右上角「账单下载」，选择时间范围，导出 Excel，邮箱接收 xlsx 文件。

**支付宝账单**

支付宝 → 我的 → 账单 → 交易流水证明，导出 csv 格式流水。

## 📌 API 接口清单

| 接口 | Method | 说明 |
| --- | --- | --- |
| `/` | GET | 前端首页 |
| `/api/upload/wechat` | POST | 上传微信 xlsx 账单 |
| `/api/upload/alipay` | POST | 上传支付宝 csv 账单 |
| `/api/bill/list` | GET | 分页查询账单流水 |
| `/api/bill/{id}` | GET | 获取单条账单 |
| `/api/bill` | POST | 手动新增账单 |
| `/api/bill/{id}` | PUT | 编辑账单 |
| `/api/bill/{id}` | DELETE | 删除账单 |
| `/api/category` | GET | 获取分类列表 |
| `/api/category` | POST | 新增消费分类 |
| `/api/stat/summary` | GET | 收支汇总统计 |
| `/api/stat/month_trend` | GET | 月度收支趋势 |
| `/api/stat/category_pie` | GET | 分类支出饼图数据 |
| `/api/stat/merchant_top` | GET | 商户消费 TOP 排行 |

## 🧪 测试要点（安装到 fnOS 后）

按官方规范验证以下项：

- 应用能正常安装、启动/停止/状态检查（应用中心显示运行状态）
- 桌面「财务统计」图标经统一网关打开 Web 页面；未登录 NAS 时访问 `/app/fn-finstat` 应被系统拦截
- 账单导入 → 流水管理 → 统计看板主流程可完成
- 上传的账单数据可正确保存、读取、更新、删除（数据写入 `TRIM_PKGVAR`）
- 升级应用后 `cmd/upgrade_callback` 正确同步依赖、历史数据保留
- 升级/卸载后用户数据保留（`uninstall_callback` 不删除 `TRIM_PKGVAR`）
- 日志写入 `${TRIM_PKGVAR}/app.log`（超 10MB 自动轮转），生命周期脚本错误写入 `TRIM_TEMP_LOGFILE`

## ⚠️ 注意事项

- FPK 打包不要包含 `.env.dev`、本地 `.local_data`、虚拟环境 `venv` 目录，也不要包含 `frontend/`（源码与 node_modules），随包只需构建产物 `app/static`；`scripts/build_fpk.sh` 已通过暂存目录机制保证这一点
- 修改前端源码后需先在 `frontend/` 执行 `npm run build` 再打包，保证 `app/static` 为最新产物
- Windows 上打包后必须修正 `cmd/` 权限位（`build_fpk.sh` 已自动调用 `scripts/fix_fpk_perm.py`）
- `cmd/` 下脚本需具备可执行权限（在 Linux/macOS 打包会自动保留；Windows 打包需先 `chmod +x cmd/main cmd/install_callback cmd/uninstall_callback`）
- 支付宝 CSV 编码为 GBK，微信 XLSX 为 Excel 二进制格式，解析器已适配
- 账单导入依靠交易号做唯一约束，重复上传相同账单会自动跳过，不会重复入库
- 飞牛OS 环境禁止写文件到 `TRIM_APPDEST`，业务数据全部存放 `TRIM_PKGVAR`（网关 Socket `app.sock` 除外，随官方示例放于 `TRIM_APPDEST`）

## 📄 License

MIT
