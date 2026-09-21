# fn-finstat 财务统计

> 部署在飞牛 OS（fnOS）上的个人收支统计应用（FastAPI + Vue 3）：导入微信/支付宝/京东/云闪付账单，关键词 + AI 双重智能分类且能随纠正自学习；AI 周期报告与财务教练、预算建议与现金流预测、账本维度与家庭共享、报销垫付与借贷台账、净资产与储蓄目标追踪，消费日历/地图/年度对比等多维可视化；账单按飞牛账号隔离，敏感配置加密存储并支持异地备份，数据全部留在自己的 NAS 上。
>
> 开发文档参考：https://github.com/LarryMKott/fnnas-docs

## ✨ 功能简介

- **账单导入**：支持微信支付 xlsx、支付宝 csv、京东金融 csv、云闪付 csv 账单自动解析（列头别名宽容匹配，随平台版本小幅变动也能导入）；导入后给出新增/跳过明细，并可导出差异报告 CSV
- **NAS 目录导入**：在导入页指定 NAS 上的账单目录后即可浏览并按文件导入，后端读取表头**自动识别来源**（微信/支付宝/京东/云闪付，内容识别不出时回退文件名关键字），不同平台的账单放同一目录也不会混；支持子目录浏览与一键批量导入全部已识别账单
- **账单导出**：流水页按当前筛选条件一键导出 xlsx / CSV（UTF-8 BOM，Excel 直接打开不乱码），与导入形成闭环
- **多账号数据隔离**：账单按登录的飞牛账号区分（统一网关转发 `X-Trim-Userid` 可信头），各账号只看到自己的流水与统计；升级前历史数据可在设置页一键认领
- **账本维度**：在账号之内再分账本（如「日常」「旅行」「装修」），流水、预算、资产快照都能归属账本；删除账本时其下数据自动并入默认账本，不会丢数据
- **数据清洗**：自动识别收支类型，交易号唯一去重，过滤转账类流水
- **分类管理**：预置消费分类，支持自定义新增分类、重命名与删除（删除后其下流水归入「其他」），内置关键词自动归类
- **分类规则自学习**：每次手动纠正归类都会沉淀为规则，之后同商户优先按已学习规则归类，优先级为**已学习规则 > 内置关键词 > AI**——越用越准，且命中规则时完全不再调用 AI；规则可在「设置」页查看、编辑目标分类与启停
- **智能分类（多供应商 AI）**：接入 OpenAI 兼容协议的大模型对关键词未命中的流水语义归类，可选 **DeepSeek（默认）/ Kimi / 智谱 GLM / 通义百炼 / SiliconFlow / OpenAI / 自部署端点**，模型名支持手填（各家上新模型不必等应用发版）；导入时可自动二次归类，也可在流水页一键重新归类存量流水；API Key 在「设置」页配置（仅管理员，应用级共享），调用失败自动回退关键词结果，不影响导入
- **AI 周期消费报告**：按月 / 季 / 半年 / 年汇总收支、分类结构与商户排行，生成 Markdown 消费分析报告（总体概览 / 结构亮点 / 环比变化 / 下月建议）；报告可归档保存（同周期覆盖旧版本），并支持从附录数字逐级下钻回流水页的对应口径
- **AI 财务教练**：对话式问账（如「我最近花得怎么样」「这笔钱该不该花」），上下文只含**结构化聚合摘要**——健康评分、固定/弹性支出结构、现金流预测、借贷应收应付、报销未到账，按问题意图裁剪携带；**原始流水永不出本机**
- **一句话查账**：把自然语言问法翻译为时间 / 分类 / 商户 / 指标 / 分组 / TOP-N 的白名单查询（规则优先 + LLM 兜底），回答附口径说明与分组占比，可「存为筛选」一键在流水页复现
- **预算管理**：按月设置总预算与分类预算，看板实时显示预算进度条与超支提示
- **预算建议与现金流预测**：按近 6 个月分类中位数给出预算建议（一次性大额按 3× 中位数剔除）；现金流预测给出未来 N 天余额曲线，区分固定项与可变支出，同时报告 P50 与 P90 口径
- **支出结构拆分**：把支出拆成**固定（必选）**与**弹性（可砍）**两类，识别依据是「每月出现 + 月度合计波动 ≤ 25%」，并列出两列清单，方便找出可削减的订阅与开销
- **储蓄目标**：为目标设定金额与目标日期，进度 = 起始日以来的累计净结余（由流水实时计算，不需要手工记账）
- **财务健康评分**：以储蓄率 / 负债率 / 应急金月数加权计算总分与分项得分，缺数据的项归一不计分，**计算口径随接口响应一并公开**（避免黑盒评分）
- **流水管理**：手动新增、编辑、删除账单记录，支持多条件分页筛选（含标签精确匹配、报销筛选、账本筛选）
- **批量操作**：流水页多选后批量改分类、打标签、标记报销、移入回收站
- **标签与报销**：流水可打多个自定义标签（逗号分隔），支持报销标记，配合筛选快速找出待报销支出
- **报销 / 垫付工作流**：把垫付的支出归入报销单 → 状态流转（待提交 / 已提交 / 已到账）→ 登记到账；报销关系独立于收支统计，统计口径不受影响
- **借贷台账**：登记借出 / 借入与多次还款，合计达到本金自动结清，删除还款或调整本金时自动回退为「进行中」；与收支统计相互独立
- **回收站**：删除的流水先进回收站（软删除），可还原或彻底删除、一键清空，防误删
- **统计看板**：收支汇总、月度趋势、分类支出饼图、商户消费 TOP 排行
- **消费日历**：按日支出热力图，全年/按月切换，一眼定位大额消费日
- **消费地图**：从商户名/备注文本推断消费城市与省份（账单本身不含地区字段），城市气泡图 + 省级分布 + 城市 TOP 榜单，并如实展示「识别率」，避免地图被误读为完整地理分布
- **年度对比**：本年 vs 去年的月度支出柱状图、同比增幅、年度汇总与逐分类对比
- **净资产追踪**：定期记录各账户资产/负债快照（存款、理财、房贷等），自动汇总净资产趋势曲线
- **家庭空间**：创建家庭后可邀请成员加入（邀请码），提供家庭月度汇总与「只读查看成员流水」能力（需家庭管理员开启明细可见）；家庭管理员负责成员与设置管理
- **共享预算（家庭）**：在个人预算之外增设一条家庭预算，全体成员的实际支出共同计入进度，仅家庭管理员可增删改
- **操作审计**：业务写操作在服务层显式打点，记录操作者、动作与前后差异摘要；管理员可查全部审计记录，普通账号只能看到自己的；审计为 best-effort（不影响主流程），保留窗口 90 天
- **自动化任务**：定时任务可在「设置」页查看开关、执行间隔、上次结果与下次执行时间，支持手动立即执行、调整间隔、启停，并可查看最近运行历史与失败原因
- **通知中心**：预算超支、接近上限、报告就绪、导入完成、任务失败、异常自检等事件逐类可开关；支持 Bark / ntfy / 企业微信 / 通用 Webhook 四种出站推送渠道；站内有未读角标与一键已读
- **异常自检**：每周自动体检一次（纯规则计算、零 AI 成本），发现「某分类支出较近 6 个月中位数抬高 1.5 倍以上」「单笔一次性大额（超分类中位数 3 倍）」「月度累计越过悲观线」「固定项疑似断供或金额突变」时按账号合并推送，同一周内不重复打扰
- **隐私加密与异地备份**：API Key、邮箱授权码、数据库口令等敏感配置**加密存储**；可配置 WebDAV 异地备份并按需推送全量备份包
- **开放 API Token**：可签发 `ffk_` 前缀的**只读**凭证供外部程序调用（明文仅签发时返回一次，服务端只存 SHA-256），恒不授予管理员权限，写方法与全部管理面接口一律拒绝——适合接入自建脚本、Home Assistant 等
- **MCP Server**：内置 MCP（Model Context Protocol）服务端点，暴露只读工具集（查流水 / 汇总 / 预算 / 储蓄目标），可被支持 MCP 的 AI 客户端直接调用；复用同一套 Token 鉴权与账号隔离，可用开关关闭
- **备份与恢复**：设置页一键下载全量数据备份（JSON，含全部账号的流水/分类/预算/资产快照/账本/家庭/报销/借贷/储蓄目标），支持合并去重恢复或覆盖恢复；三种数据库通用
- **移动端 PWA**：支持添加到手机主屏幕（manifest + Service Worker），静态资源离线缓存，业务接口永不缓存
- **主题切换**：顶栏一键切换 跟随系统 / 浅色 / 深色，并可叠加多套配色主题（含「极光」科技感），两个维度正交组合；auto 模式随系统主题实时联动，偏好本地记忆、首屏无闪烁，图表配色同步切换
- **多类型数据库**：安装向导可选 SQLite（默认）/ MySQL / PostgreSQL，连接参数随向导收集；ORM 模型（SQLAlchemy）统一三方言
- **设置页迁移数据库**：「设置」页可随时把现有数据一键迁移到新的 MySQL / PostgreSQL 数据库并立即切换，原库保留可回退，无需重装重启（预算、资产快照等一并搬移）
- **运行日志查看**：「设置」页可查看应用运行日志末尾若干行（含账单导入、智能分类等过程记录）并下载完整日志文件
- **检查更新**：「设置」页「关于」卡片可一键检查是否有新版本（比对项目 Release 的正式版 / 测试版渠道，展示版本差异与更新说明，并给出 fpk 下载与 MD5 校验入口）；只做「检查 + 引导下载」——飞牛第三方应用的安装升级由应用中心完成，应用不自行替换安装目录。离线环境下以可读提示降级，不影响应用使用
- **服务端口可自定义**：向导可配置 HTTP 服务端口（`wizard_port`，默认 8090），避免与其它程序端口冲突；fnOS 统一网关模式经 Unix Socket 通信，不占用 TCP 端口
- **卸载可选清除数据**：卸载时可选保留数据（默认，重装续用）或彻底清除本地账单数据；外部数据库数据不受影响
- **接口地址可配置**：前后端接口地址前缀随向导设置（前端运行时自动适配，改前缀无需重新构建）
- **存储**：SQLite 单文件数据库或外部数据库；飞牛OS 环境使用 `TRIM_PKGVAR` 持久目录，升级/卸载保留用户数据

## 📁 项目结构

```
fn-finstat/
├── manifest                  # FPK 应用清单（fnOS 官方规范，INI 格式）
├── assets/icons/             # 应用图标源（中性打包图标 + 日间/夜间主题图标，scripts/make_icons.py 生成）
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
│   ├── start.sh              # 本地开发启动脚本（生产环境由 cmd/main 负责）
│   └── …                     # 样例账单/图标生成、打包、测试与自检脚本
├── app/                      # 主应用源码目录
│   ├── main.py               # FastAPI 入口（路由挂载、全局异常处理器、HTTP 中间件、静态资源）
│   ├── config.py             # 静态配置：fnOS 环境变量/向导参数（启动时定型，编号分节）
│   ├── file_settings.py      # 运行期文件配置：AI/NAS 目录/通知（设置页读写、立即生效）
│   ├── requirements.txt      # Python 依赖
│   ├── core/                 # 核心层：统一错误码/业务异常族、请求上下文、异常处理器、HTTP 中间件（观测/安全头/权限门禁）
│   ├── db/                   # 数据库层
│   │   ├── engine.py         # 引擎构建、运行期切换与会话管理（三方言）
│   │   ├── migrations.py     # schema 版本迁移（v1 → v15）
│   │   ├── base.py           # 建库初始化与数据库类型标记（engine/migrations 门面）
│   │   └── dao/              # 数据访问层 DAO（bill / category / budget / asset / stat / ai_report /
│   │                         #   task / notify / learned_rule / ledger / family / reimb / loan /
│   │                         #   audit / savings / api_token）
│   ├── services/             # 业务逻辑层（33 个模块：导入、统计、预算、预测、账本、家庭、报销、
│   │                         #   借贷、审计、储蓄、AI 分类/报告/教练、通知、异常自检、备份…统一抛 core.errors 异常族）
│   ├── parsers/              # 账单解析器（平台注册表 + 抽象基类 + 来源识别）
│   ├── api/                  # FastAPI 路由接口（26 个域模块，统一 {code,msg,data} 响应包装）
│   ├── schemas/              # Pydantic 请求/响应模型
│   ├── utils/                # 纯工具（金额、周期、筛选、上传、关键词归类、地域推断、加密）
│   ├── ui/                   # fnOS 桌面入口配置与图标
│   └── static/               # 前端构建产物（由 frontend/ 构建生成，整目录 gitignore，请勿手改）
├── frontend/                 # 前端源码（Vue 3 SFC + Vite 工程）
│   ├── src/api/              #   接口层：统一请求封装 + 按业务域端点模块
│   ├── src/composables/      #   组合式函数（useChart / useConfirm / useLoading）
│   ├── src/utils/            #   共享工具（格式化、日期、常量、图表主题）
│   ├── src/assets/styles/    #   样式域拆分（令牌 / 配色主题 / 各域样式）
│   ├── src/components/       #   面板组件；大面板按域拆子组件（settings/ import/）
│   └── vite.config.js        #   构建配置（产物直接输出至 app/static，SW 版本自动写入）
├── .env.dev                  # 本地开发环境变量（不打包进 FPK）
└── docs/                     # 项目文档（索引见 docs/README.md）
    ├── README.md             #   文档索引与命名/元信息约定
    ├── 项目需求文档.md        #   需求基线 v1.0（对应 0.4.0 现状）
    ├── 重构说明.md            #   工程规范与重构说明（接口契约/错误码/分层/验证）
    ├── 后端架构设计方案.md    #   后端架构现状（分层、中间件链、数据层、各子系统）
    ├── 前端架构设计方案.md    #   前端架构现状（技术选型、竞态治理、主题体系、构建）
    ├── 界面设计方案.md        #   设计系统规范（温暖金融科技）
    ├── 界面设计方案预览.html  #   高保真预览页（可切深浅主题）
    ├── 飞牛主题适配与图标方案.md
    ├── 发布流程与Release日志.md #  Gitee Go 双流水线、版本号与 tag 策略
    ├── 开发规范/             #   Git 提交 / 前端 / 后端 / 编译与构建规范
    ├── devlog/               #   开发日志（按日期归档，含评审与修复记录）
    ├── diagrams/             #   交互式架构图与流程图（自包含 HTML + 源 JSON）
    ├── archive/              #   历史存档（v0.1 需求文档）
    └── images/               #   截图与图标预览图
```

> 📚 **文档索引**：全部文档的分类、状态与维护约定见 `docs/README.md`。改动文档后请同步更新该索引。

> **与早期草案的差异说明**：草案中的 `fnpack.json` 在 fnOS 官方规范中不存在——官方以 `manifest` 文件作为应用清单，且打包检查强制要求 `config/privilege`、`config/resource`、`ICON.PNG`、`ICON_256.PNG`、`wizard/` 等文件。本项目按官方规范落地：生命周期脚本采用官方命名 `cmd/main` / `cmd/install_callback` / `cmd/uninstall_callback`，本地开发启动脚本放 `scripts/start.sh`（不随包发布）；Python 运行时按官方规范声明 `install_dep_apps=python312` 并在脚本中自动加入 PATH。

## 🧰 环境依赖

- **本地开发**：Python >= 3.9（自备），系统依赖 `python3 python3-pip`
- **前端构建**：Node.js >= 18 + npm（仅修改 `frontend/` 前端源码后重新构建时需要）。本地构建直接使用全局安装（PATH）里的 Node，需满足 Vite 8 的 `^20.19.0 || >=22.12.0` 要求；`ci_build.sh` 只在环境缺少 Node >= 18 时才自举下载（版本默认 24.18.0，可用 `NODE_VERSION` 覆盖），Gitee 流水线镜像则在 `.workflow/build-fpk.yml` 的 `nodeVersion` 固定
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
uvicorn app.main:app --host 127.0.0.1 --port 8090
# 或使用启动脚本（默认同样只绑回环）：bash scripts/start.sh

# 需要手机等局域网设备访问时显式放开（仅在可信网络使用）：
#   uvicorn app.main:app --host 0.0.0.0 --port 8090
#   HOST=0.0.0.0 bash scripts/start.sh
```

> ⚠️ **不要随意绑 `0.0.0.0`**：应用把 `X-Trim-Userid` / `X-Trim-Isadmin` 请求头当作
> 飞牛网关注入的可信身份（见 `app/api/deps.py`），绑到 `0.0.0.0` 时同网段任何人都能
> 伪造这两个头直接成为管理员（导出全量备份、覆盖恢复数据、改配置、下载日志）。
> fnOS 生产环境走 Unix Socket（`cmd/main` 的 `uvicorn --uds`），不受此影响。

3. 访问地址：

- 主页：http://127.0.0.1:8090/app/fn-finstat/（与生产统一网关同前缀；访问 http://127.0.0.1:8090/ 亦可）
- 接口文档：http://127.0.0.1:8090/docs

本地数据存放于项目根目录 `.local_data`，临时文件在 `.local_tmp`。

### 生成样例账单（可选）

```bash
python scripts/gen_sample_bills.py
```

生成微信/支付宝样例账单至 `.local_tmp/`，可在「账单导入」页面上传验证。

### API 冒烟测试（可选）

针对**真实运行的服务进程**做一轮端到端冒烟（pytest 单测走 TestClient 不占真实端口，此脚本与之互补）。先启动本地服务（`scripts/start.sh`），再运行：

```bash
python scripts/gen_sample_bills.py        # 先生成样例账单
python scripts/smoke_test.py [base_url]   # 默认 http://127.0.0.1:8090，仅允许本机/内网地址
```

覆盖首页、分类、导入（微信/支付宝）、流水 CRUD、筛选分页、统计报表与异常分支。

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

### AI 能力与多供应商配置

「设置」页可配置 AI 能力（`/api/ai/*`），支持**多家供应商**：

1. 在所选供应商的控制台创建 API Key → 「设置」页选择**供应商**（DeepSeek 默认 / Kimi（Moonshot）/ 智谱 GLM / 通义百炼 / SiliconFlow / OpenAI / 自定义端点）→ 填入 **API Key** → **测试连接** → **保存配置**（保存仅限管理员：Key 为全应用共享）
2. **导入时自动智能分类**（开关）：导入账单先走已学习规则与内置关键词归类，仍未命中（分类为「其他」）的记录自动交给大模型语义归类，控制调用量与费用
3. **流水页「AI 智能分类」按钮**：把当前账号存量流水中分类为「其他」的记录批量重新归类（单次最多 1000 条、限时约 4 分钟，超时未处理完可再次点击续跑）

细节规则：

- 配置持久化于 `TRIM_PKGVAR/finance/ai_config.json`（API Key 界面只回显掩码），本地开发可用环境变量 `DEEPSEEK_API_KEY` 兜底
- **供应商注册表**（`app/file_settings.py` 的 `AI_PROVIDERS`）内置各家的默认 API 地址与常用模型；切换供应商时若未显式指定地址/模型，会自动回填该供应商的预置默认值，避免「供应商=智谱、地址仍是 DeepSeek」这类错配
- **模型名恒可手填**：预置模型只是快捷选项（下拉 + 手输），各家上新模型无需等待应用发版跟进；错误提示会带供应商名（如「智谱 GLM 接口返回 401」）便于定位
- 底层统一走 OpenAI 兼容协议（`{base_url}/chat/completions` + Bearer），分类、周期报告、财务教练、一句话查账、测试连接五个消费方共用同一调用收口（`ai_service.chat`）；禁用重定向，防止 SSRF
- AI 只能返回候选分类（当前分类表）中的名称，返回编造分类会被丢弃、保留原分类
- 批量按 50 条/请求分组、单批失败只影响当批；导入阶段整体限时约 90 秒，超时部分保留关键词结果，导入永不因 AI 失败而失败
- 调用为同步请求，会产生少量 API 费用（仅对「其他」分类记录生效，量小费用低）
- **隐私红线**：全部 AI 功能（含财务教练）只发送**结构化聚合摘要**——金额汇总、占比、评分等，**原始流水明细永不出本机**

### AI 周期消费报告

统计看板右上角「AI 报告」入口：

- **周期可选月 / 季 / 半年 / 年**：后端汇总所选周期的收支汇总、环比上期、分类支出对比、商户 TOP5 与单日最高支出，交给大模型生成 Markdown 报告（总体概览 / 结构亮点 / 环比变化 / 下期建议）
- **生成与归档分离**：`POST /api/ai/report/generate` 生成预览、不落库；确认满意后再 `POST /api/ai/report/archive` 归档（按周期唯一键覆盖旧版本），归档报告可在报告列表中回看与删除
- **精细级追溯**：报告附录中的数字可按「周期 + 类型 + 分类/商户」确定性映射回流水页，点击即带对应筛选条件跳转，逐笔核对
- 未配置 API Key 时明确提示先到设置页配置；报告需手动点击「生成」才发起调用（调用产生 API 费用）

### AI 财务教练与一句话查账

- **财务教练**（`POST /api/coach/chat`）：对话式问账，上下文按问题意图裁剪携带——财务健康评分、固定 vs 弹性支出结构、现金流预测 P50/P90、借贷应收应付、报销未到账，全部复用**已经算好的后端结果**，只做文本拼装；问「负债」才带借贷板块，问「订阅」才带支出结构
- **一句话查账**（`POST /api/nl-query`）：把「上个月吃饭花了多少」这类问法翻译为时间 / 分类 / 商户 / 指标 / 分组 / TOP-N 的白名单查询。**规则优先**（命中即零 AI 成本），未命中才交给 LLM 兜底，且 LLM 输出必须通过白名单校验
- 回答以卡片呈现：结论 + 口径 chip + 分组占比条 + 依据折叠；可「存为筛选」一键在流水页复现同一口径，支持最多 3 轮追问（追问回传原始 spec 重过白名单，指代继承不额外调用 LLM）

### 预算建议与现金流预测

- **预算建议**（`/api/forecast/budget-suggestions`）：按近 6 个月各分类支出的中位数给建议额度；一次性大额按「月度中位数 × 3」剔除后再统计，避免单笔大额把建议值抬高
- **现金流预测**（`/api/forecast`）：给出未来 N 天的余额曲线，同时报告 P50（中位）与 P90（悲观）两种口径；起点余额支持「快照补记」与「流水净额」双口径
- **固定项识别**：判定条件是「每月都出现 + 月度合计波动 ≤ 25%」，识别结果同时驱动预测与支出结构拆分
- **支出结构拆分**（`/api/forecast/expense-structure`）：把支出分成固定（必选）与弹性（可砍）两类，看板现金流卡片内展示必选 / 可砍占比与两列清单

### 家庭空间与共享预算

- **创建 / 加入**：创建家庭后生成邀请码，其他飞牛账号凭邀请码加入；家庭管理员可移除成员、调整家庭设置、解散家庭（解散后家庭数据清空）
- **家庭月度汇总**（`/api/family/summary`）：聚合全体成员的收支视图，不必逐个账号查看
- **只读查看成员流水**（`/api/family/members/{user_id}/bills`）：需家庭管理员显式开启「明细可见」，默认关闭；查看为只读，不提供代改
- **共享预算**（`/api/family/budgets`）：个人预算之外再设一条家庭预算，全体成员的实际支出共同计入进度；仅家庭管理员可增删改

### 报销 / 垫付与借贷台账

- **报销 / 垫付工作流**（`/api/reimb/*`）：新建报销单 → 把勾选的支出流水归入 → 状态流转（待提交 / 已提交 / 已到账）→ 到账登记校验；报销关系独立于收支统计，**统计口径不受影响**（报销标记只是标签维度）
- **借贷台账**（`/api/loans/*`）：登记借出 / 借入（对方、本金、日期、备注）与多次还款；合计达到本金自动结清，删除还款或调整本金后自动回退为「进行中」；与收支统计相互独立，不做现金流抵减

### 操作审计

业务写操作在服务层显式打点（`/api/audit`），覆盖全部业务写域：

- 每条记录含操作者账号、动作、目标与**前后差异摘要**（diff）
- 打点为 best-effort：审计写入失败不影响主流程
- 保留窗口 **90 天**，超期自动清理；审计表不参与备份导出，覆盖恢复也不会清空审计历史
- 查询权限：管理员看全部，普通账号只能看到自己的操作

### 自动化任务与通知中心

- **自动化任务**（`/api/settings/automation`）：定时任务统一在「设置」页管理——查看名称 / 开关 / 执行间隔 / 上次结果 / 下次执行时间，支持调整间隔、启停、手动立即执行（后台异步），并可查看最近运行历史与失败原因
- **通知中心**：站内通知含未读角标（`/api/notifications/unread-count` 为轮询用轻量接口）与一键已读
- **出站推送**：支持 Bark / ntfy / 企业微信 / 通用 Webhook 四种渠道，可按事件逐类开关（预算超支、接近上限、报告就绪、导入完成、任务失败、异常自检等）
- **异常自检**（每周一次，纯规则计算、零 AI 成本）：发现「某分类当月支出 > 近 6 个完整月月中位数 × 1.5」「单笔一次性大额（> 分类中位数 × 3）」「当月累计支出越过悲观线（可变支出 P90 + 固定项合计）」「固定项疑似断供（过扣款日 3 天仍无流水）或金额突变（偏离月均 ±25%）」时告警；单周期异常按账号合并为一条通知，同一 ISO 周内不重复推送，单条通知最多列 8 行

### 隐私加密与异地备份

- **敏感配置加密存储**：AI API Key、邮箱授权码、数据库口令等在落盘前加密，不再以明文写入配置文件
- **异地备份**（`/api/remote-backup/*`）：配置 WebDAV 目标后可按需推送全量备份包；密码加密存储、界面不回传
  - `GET / PUT /api/remote-backup/config` 读写配置（仅管理员）
  - `POST /api/remote-backup/push` 立即推送当前全量备份

### 开放 API Token 与 MCP Server

- **只读 Token**（`/api/tokens`）：签发 `ffk_` 前缀的凭证供外部程序调用。明文**仅在签发时返回一次**，服务端只存 SHA-256 摘要；Token 恒不具备管理员身份，**写方法与全部管理面接口一律拒绝**（返回 403）；可随时撤销，撤销立即失效
- 适用场景：自建脚本拉取流水、接入 Home Assistant / 其他自动化工具、喂给支持 MCP 的 AI 客户端
- **MCP Server**（`POST /api/mcp`）：内置 Model Context Protocol 端点，手写 JSON-RPC 2.0（零新增运行时依赖），实现 `initialize` / `tools/list` / `tools/call`，暴露只读工具集（查流水 / 汇总 / 预算 / 储蓄目标）
- MCP 复用与 Token 相同的鉴权与账号隔离，可用 `MCP_ENABLED` 开关关闭

### 备份与恢复（设置页）

- **下载备份**（`GET /api/settings/backup`）：导出全部账号的分类、流水、预算、资产快照、账本、家庭、报销、借贷、储蓄目标等业务数据为一个 JSON 文件（审计表除外）
- **权限**：备份导出与恢复（尤其覆盖模式会清空全部账号数据）仅限管理员账号操作；本地独立部署（无网关身份头）视为唯一用户不受限
- **恢复**（`POST /api/settings/restore`，上传 JSON）：
  - **合并模式**（默认）：按唯一键去重导入（流水 tx_id / 分类名 / 预算唯一键）；无交易号的流水无法去重，重复恢复同一备份可能产生重复记录
  - **覆盖模式**：先清空全部业务表再导入（不可恢复，界面需二次确认）
  - 单行格式非法自动跳过并在结果中计数；流水引用的分类不存在时自动补建
- **跨版本与跨库兼容**：备份文件带 `format_version`，恢复时按版本做字段重映射（账本、家庭、报销、借贷等新增维度按名称重新关联），三种数据库之间互通

### 预算管理

- 看板「预算进度」区按月管理：可设置一条**总预算**（不选分类）与任意多条**分类预算**
- 进度条实时对比当月实际支出：接近超支变橙色、已超支变红色并标注「已超支」
- 总预算行对比当月全部支出；未设总预算行时顶部汇总显示各分类预算之和

### 回收站与批量操作

- 「流水管理」页删除为**软删除**：流水移入回收站，可单条/批量还原、彻底删除或一键清空
- 多选流水后可批量：改分类、打标签（覆盖原标签）、标记/取消报销、移入回收站（单次最多 1000 条）
- 标签按中英文逗号/分号拆分、去空去重后以逗号存储；筛选时按标签**精确匹配**（避免子串误命中）

### 净资产追踪（资产管理）

- 「资产管理」页定期记录快照：日期 + 账户条目名 + 类型（资产/负债）+ 金额（负债记正数）
- 趋势图按快照日期汇总资产、负债与净资产（净资产 = 资产 - 负债），按当前账号隔离
- 快照数据在跨库迁移/备份恢复时随流水一并搬移

### 移动端 PWA

- 前端构建产物包含 `manifest.webmanifest`、`sw.js` 与图标；浏览器（手机/桌面 Chrome、Edge、Safari）访问后可「添加到主屏幕/安装应用」，以独立窗口全屏打开
- Service Worker 策略：`/api/` 请求永不缓存；带内容哈希的静态资源缓存优先；页面导航网络优先，离线或后端 5xx 时回退缓存
- `sw.js` / `manifest.webmanifest` / 图标由应用根路由直接提供（Service Worker 必须位于应用根作用域才能控制整页）；`SW_VERSION` 由构建自动盖章，**不要手工改**——漏改会让旧构建资源在离线缓存中残留堆积
- 经飞牛统一网关（HTTPS）访问时安装体验最佳；注册失败不影响任何功能

### 运行日志（设置页）

「设置」页底部可查看与下载应用运行日志（`/api/settings/logs*`），日志覆盖应用启动、HTTP 访问、账单导入全过程（开始导入、解析/归类/入库统计、失败原因）与智能分类（AI 归类条数、批次失败原因）：

- 界面按需加载（首次切到设置页才拉取），可选最近 100 / 300 / 1000 行，等宽字体展示并自动滚动
- 日志路径统一为 `config.LOG_PATH`（环境变量 `LOG_FILE` 优先，fnOS 由 `cmd/main` 注入；本地默认项目根 `app.log`），写入与查看共用同一来源
- 大文件只读尾部 1MB 再截取末尾 N 行，避免整文件读入内存；`Content-Length` 与内容错位的流式竞态已规避（快照式下载）
- 下载接口返回当前日志文件快照（不含轮转备份 `.log.1`～`.log.3`）

### 升级与数据迁移

应用内置 schema 版本管理（`app/db/base.py`），升级安装后首次启动自动完成数据迁移，无需人工介入：

- **版本记录**：数据库内的 `app_meta` 表记录 `schema_version`。0.2.x 老库无此表，升级后按基线版本补记，历史数据原样保留
- **逐版本迁移**：新版本 schema 变更以迁移函数登记（`_MIGRATIONS`，每个函数负责 vN → vN+1），启动时按序应用；每个迁移独立事务并立即写版本戳，中断后重启自动从断点续迁
  - v2：bills 增加 `user_id` 列（多账号隔离）
  - v3：bills 增加 `tags` / `reimbursed` / `deleted` 列（标签、报销、回收站），新增 `budgets`（预算）与 `asset_snapshots`（资产快照）表
  - v4：自动化底座新表 `scheduled_tasks` / `task_runs` / `imported_files`
  - v5：AI 报告归档表 `ai_reports`
  - v6：通知中心 `notifications` / `notification_reads`
  - v7：分类自学习 `learned_rules`
  - v8：**首个破坏性 schema 变更** —— 账本维度 `ledgers` 表 + 三张业务表加 `ledger_id`（升级等价性靠「默认账本 + 列级默认值」保证）
  - v9：家庭空间 `families` / `family_members`
  - v10：家庭预算 —— `budgets` 加可空列 `family_id`（家庭行合成属主复用唯一键，规避与个人预算冲突）
  - v11：报销 / 垫付 `reimbursements` + `bills.reimb_id`
  - v12：借贷台账 `loans` / `loan_payments`
  - v13：操作审计 `audit_logs`
  - v14：储蓄目标 `savings_goals`
  - v15：开放 API Token `api_tokens`（当前 `LATEST_SCHEMA_VERSION`）
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

卸载应用时会出现「数据清理」向导，由用户在两个选项中显式选择（`wizard_purge_data`）：

- **保留账单数据（推荐，默认）**：账单数据库、数据库切换配置与应用日志保留在 `TRIM_PKGVAR` 持久目录，重新安装后自动沿用
- **删除全部本地数据（不可恢复）**：清除 `TRIM_PKGVAR/finance`（SQLite 数据库与切换配置）、应用日志与运行虚拟环境
- 存放在外部 MySQL / PostgreSQL 中的数据不受卸载影响，需自行管理；向导页也会提示先完成迁移或备份

只有明确命中「删除」取值时才会删数据，向导未回传或取值异常时一律按保留处理。

### 前端开发与构建

前端源码位于 `frontend/`（Vue 3 SFC + Vite），构建产物输出到 `app/static/`（FastAPI 托管目录，勿手改）：

```bash
cd frontend
npm install
npm run build    # 产物输出至 ../app/static/
```

> **测试与安全扫描须知**：`app/static/` 整体是构建产物——`assets/` 为内容哈希命名的压缩包（已 gitignore 不入库），`index.html`/`sw.js`/`manifest.webmanifest`/`icons` 是可由 `frontend/` 再生的产物模板（`sw.js` 每次构建被盖时间戳）。单元测试不依赖该目录；扫描器对 `assets/` 内压缩包命中的 SSRF/注入类告警是对第三方压缩代码的误报（浏览器端静态资源，无服务端执行），处置方式是说明而非改码。相关不变量由 `tests/platform/test_static_artifacts.py` 固化。

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
# 仅限本地调试，可跳过测试门禁（勿用于发布）：
SKIP_TESTS=1 bash scripts/build_fpk.sh
# 打测试版（版本号带 -dev 后缀、产物名为 fn-finstat-dev.fpk）：
BUILD_CHANNEL=dev bash scripts/build_fpk.sh
```

Windows 也可用原生 cmd 脚本（双击 `scripts\build_fpk.bat` 即可，无需 Git Bash；同样支持 `FNPACK` / `PYTHON` / `SKIP_TESTS` 环境变量，测试门禁自动复用 `run_tests.bat`，Python 优先项目 `app/venv`）。两个脚本流程与产物完全一致，打包自检共用 `scripts/fpk_selfcheck.py`。

脚本流程：**单元测试门禁（全部通过才继续）** → 组装干净暂存目录（只含打包必需文件，**排除** `app/venv`、`frontend/`、`.local_*`、`__pycache__`）→ `fnpack build` → 修正 Windows 打包丢失的 `cmd/` 可执行权限位（0666 → 0755）。

产物在项目根目录（约 380KB，platform 声明为 all，无架构后缀），同一渠道产出三个内容相同、用途不同的文件：

| 文件 | 说明 |
| --- | --- |
| `fn-finstat.fpk` | fnpack 原始输出 |
| `fn-finstat-latest.fpk`（正式）/ `fn-finstat-dev.fpk`（测试） | 按渠道区分的**固定入口**，直接拿这个装 |
| `fn-finstat-v{版本}.fpk` | 带完整版本号的副本，用于追溯具体构建 |
| `MD5SUMS.txt` | 上面**两个交付产物**的 MD5，`md5sum -c MD5SUMS.txt` 可校验下载完整性 |

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

> 说明：统一网关校验 NAS 登录态并转发身份头（`X-Trim-Userid` 等），业务数据按账号隔离（消费分类全局共享）；备份导出/恢复与 AI Key、NAS 目录等全局配置仅限管理员。多账号场景请勿绕过网关直接开放 HTTP 端口（身份头可伪造，见下文安全提示）。

### 打包检查项（fnpack build 自动校验）

| 路径 | 说明 |
| --- | --- |
| `manifest` | 应用清单，含必要字段 |
| `config/privilege` | 运行用户，合法 JSON |
| `config/resource` | 资源声明，合法 JSON |
| `ICON.PNG` / `ICON_256.PNG` | 打包图标（包内布局；源文件在 `assets/icons/`，另有 `ICON_LIGHT*`/`ICON_DARK*` 主题图标，均由 `scripts/make_icons.py` 生成） |
| `app/`、`cmd/`、`wizard/` | 必选目录 |
| `app/ui/` | 声明 `desktop_uidir=ui` 时必须存在 |

## 📂 NAS 目录导入

导入页顶部「NAS 目录导入」：填入 NAS 上存放账单的**绝对路径**（如 fnOS 的 `/vol1/1000/bills`、Windows 本地开发的 `D:/bills`）保存后即可浏览目录并导入。

- **来源自动识别**：后端读取文件头部样例，按各平台表头特征判定来源（微信/支付宝/京东/云闪付），文件名关键字兜底（如 `京东金融流水.csv`）；识别不出的文件标记「未识别」，可修改文件名重试
- **目录浏览**：支持进入子目录、返回上一级；仅展示 csv / xlsx 账单文件，隐藏文件与其他后缀不出现
- **一键批量导入**：把已识别来源的文件依次导入并汇总结果；单个失败不影响其余文件
- **安全边界**：访问路径限制在配置的账单目录之内（含符号链接解析后的校验），单文件大小上限与上传一致（10MB）
- **权限（fnOS，v0.5.1+）**：两条授权路径，任选其一，系统要求 fnOS ≥ 1.2.0401
  - **应用共享授权（推荐）**：管理员在飞牛「系统设置 > 应用 > 财务统计」里添加允许访问的文件夹，
    或在导入页点「添加共享目录」直接授权。授权后的目录会列在导入页，点「设为账单目录」即可使用；
    应用级生效，所有使用者共享
  - **用户个人授权**：在导入页点「申请授权目录」，当前登录用户自己选择授权给应用的目录（按飞牛账号区分）
  - 未授权时目录对应用不可读，列表区会给出提示；不满足版本要求时授权区自动隐藏，不影响手动填目录导入
- 目录配置存于应用数据目录 `nas_config.json`，与数据库配置同策略持久保留

## 📝 账单导出说明

**微信支付账单**

微信 → 我 → 服务 → 钱包 → 账单 → 右上角「账单下载」，选择时间范围，导出 Excel，邮箱接收 xlsx 文件。

**支付宝账单**

支付宝 → 我的 → 账单 → 交易流水证明，导出 csv 格式流水。

**京东金融账单**

京东金融 App → 我的 → 账单 / 收支统计 → 导出流水，接收 csv 文件。

**云闪付账单**

云闪付 App → 首页「账单」→ 筛选后导出交易明细，接收 csv 文件。

> 京东/云闪付解析器按列头别名宽容匹配（如「交易金额 / 金额(元)」「流水号 / 订单号」），平台列头随版本微调一般无需改代码；交易关闭类流水自动跳过。

## 📌 API 接口清单

> 下表路径统一省略统一网关前缀。生产环境经飞牛网关访问时前缀为 `/app/fn-finstat`（如 `/app/fn-finstat/api/bill/list`），独立部署/本地开发可自定义前缀，前端运行时自动推导；接口文档见 `/docs`。

### 账单导入

| 接口 | Method | 说明 |
| --- | --- | --- |
| `/api/upload/wechat` | POST | 上传微信 xlsx 账单 |
| `/api/upload/alipay` | POST | 上传支付宝 csv 账单 |
| `/api/upload/jd` | POST | 上传京东金融 csv 账单 |
| `/api/upload/unionpay` | POST | 上传云闪付 csv 账单 |
| `/api/upload/export-details` | POST | 导出导入差异报告为 CSV |
| `/api/nas/config` | GET / PUT | NAS 账单目录配置（绝对路径，存 nas_config.json；保存仅管理员） |
| `/api/nas/files` | GET | 浏览账单目录（仅子目录与 csv/xlsx 文件，文件附自动识别的来源） |
| `/api/nas/import` | POST | 导入目录内文件（自动识别来源，路径限制在账单目录内） |
| `/api/nas/authorization` | GET | **飞牛环境**：当前用户已授权的账单目录 + 管理员授权的共享目录（`available/authorized/folders/reason/uid/shared_folders/shared_reason/is_admin`）。共享目录查询失败只影响 `shared_*` 字段，不拖垮整体；非飞牛环境一律 200 + `available=false` + `reason`，不抛 5xx |
| `/api/nas/authorization/check-acl` | POST | **飞牛环境**：对账单目录内路径做可读/可写/可删检查，返回 `{path:{readable,writable,deletable}}`；网关不可用时全部按 `true` 放行。单次最多 200 个路径 |

### 流水管理

| 接口 | Method | 说明 |
| --- | --- | --- |
| `/api/bill/list` | GET | 分页查询账单流水（支持标签/报销/账本等筛选） |
| `/api/bill/export` | GET | 按筛选条件导出流水（format=xlsx/csv，不含回收站） |
| `/api/bill/batch` | POST | 批量操作（改分类/打标签/报销标记/删除） |
| `/api/bill/recycle` | GET / DELETE | 回收站列表 / 彻底删除选中流水 |
| `/api/bill/recycle/restore` | POST | 从回收站还原流水 |
| `/api/bill/recycle/empty` | POST | 清空回收站 |
| `/api/bill` | POST | 手动新增账单（归属当前账号） |
| `/api/bill/{bill_id}` | GET / PUT / DELETE | 获取单条 / 编辑 / 删除（移入回收站） |

### 分类、预算与账本

| 接口 | Method | 说明 |
| --- | --- | --- |
| `/api/category` | GET / POST | 分类列表 / 新增消费分类 |
| `/api/category/{category_id}` | GET / PUT / DELETE | 分类详情（含流水数量）/ 重命名（同步更新流水）/ 删除（其下流水归入「其他」） |
| `/api/budget` | GET / PUT | 某月预算进度总览（含各分类实际支出）/ 新增修改（按月+分类 upsert，空分类=总预算） |
| `/api/budget/{budget_id}` | DELETE | 删除预算（仅当前账号） |
| `/api/ledgers` | GET / POST | 获取账本列表 / 新建账本 |
| `/api/ledgers/{ledger_id}` | PUT / DELETE | 改名、改备注 / 删除账本（数据并入默认账本） |

### 统计、预测与健康评分

| 接口 | Method | 说明 |
| --- | --- | --- |
| `/api/stat/summary` | GET | 收支汇总统计（当前账号） |
| `/api/stat/month_trend` | GET | 月度收支趋势 |
| `/api/stat/category_pie` | GET | 分类支出饼图数据 |
| `/api/stat/merchant_top` | GET | 商户消费 TOP 排行 |
| `/api/stat/daily_heatmap` | GET | 按日收支汇总（消费日历热力图） |
| `/api/stat/region_map` | GET | 消费地图：按省级行政区聚合支出（文本推断，含识别率） |
| `/api/stat/year_comparison` | GET | 年度对比报表（本年 vs 去年） |
| `/api/stat/health` | GET | 财务健康评分（储蓄率 / 负债率 / 应急金月数加权，**口径随响应公开**） |
| `/api/forecast` | GET | 现金流预测：未来 N 天余额曲线 |
| `/api/forecast/budget-suggestions` | GET | 预算建议（近 6 个月分类中位数） |
| `/api/forecast/expense-structure` | GET | 固定支出 vs 弹性支出拆分（近 6 个完整月，必选项 / 可砍项） |

### 资产与储蓄目标

| 接口 | Method | 说明 |
| --- | --- | --- |
| `/api/asset` | GET / POST | 资产快照列表（新的在前）/ 新增快照 |
| `/api/asset/trend` | GET | 净资产趋势（按快照日期汇总） |
| `/api/asset/{asset_id}` | PUT / DELETE | 编辑 / 删除资产快照 |
| `/api/savings-goals` | GET / POST | 储蓄目标列表（进度 = 起始日以来累计净结余，由流水实时计算）/ 新建目标 |
| `/api/savings-goals/{goal_id}` | PUT / DELETE | 更新目标（名称/金额/目标日期/备注）/ 删除（不影响流水） |

### 家庭空间

| 接口 | Method | 说明 |
| --- | --- | --- |
| `/api/family` | GET / POST / DELETE | 我的家庭信息 / 创建家庭 / 解散家庭（家庭管理员） |
| `/api/family/join` | POST | 凭邀请码加入家庭 |
| `/api/family/leave` | POST | 退出家庭 |
| `/api/family/invite/regenerate` | POST | 重新生成邀请码（家庭管理员） |
| `/api/family/members/{user_id}` | DELETE | 移除成员（家庭管理员） |
| `/api/family/members/{user_id}/bills` | GET | 查看成员流水（只读，需家庭开启明细可见） |
| `/api/family/settings` | PUT | 更新家庭设置（家庭管理员） |
| `/api/family/summary` | GET | 家庭月度汇总（聚合视图） |
| `/api/family/budgets` | GET / PUT | 某月家庭预算进度总览（全体成员）/ 新增修改家庭预算（仅家庭管理员） |
| `/api/family/budgets/{budget_id}` | DELETE | 删除家庭预算（仅家庭管理员，限本家庭） |

### 报销 / 垫付与借贷台账

| 接口 | Method | 说明 |
| --- | --- | --- |
| `/api/reimb` | GET / POST | 报销单列表（含流水笔数与金额合计）/ 新建报销单（待提交） |
| `/api/reimb/{claim_id}` | PUT / DELETE | 更新（名称/备注/状态流转/到账登记）/ 删除报销单（其下流水摘除） |
| `/api/reimb/{claim_id}/bills` | GET / POST / DELETE | 报销单内流水明细 / 加入勾选的支出流水 / 摘除流水（报销标记复位） |
| `/api/loans` | GET / POST | 借贷台账（含应收 / 应付汇总与逐条进度）/ 登记一笔借出、借入 |
| `/api/loans/{loan_id}` | PUT / DELETE | 更新借贷信息（对方/本金/日期/备注）/ 删除借贷及其全部还款记录 |
| `/api/loans/{loan_id}/payments` | GET / POST | 还款明细与进度 / 登记还款（合计达本金自动结清） |
| `/api/loans/{loan_id}/payments/{payment_id}` | DELETE | 删除一条还款记录（不足本金后回到进行中） |

### 自动化任务与通知中心

| 接口 | Method | 说明 |
| --- | --- | --- |
| `/api/settings/automation` | GET | 定时任务列表（名称/开关/间隔/上次结果/下次执行时间） |
| `/api/settings/automation/{task_key}` | PUT | 调整执行间隔（分钟） |
| `/api/settings/automation/{task_key}/toggle` | POST | 启用/停用任务（停用后不再自动调度，可手动执行） |
| `/api/settings/automation/{task_key}/run` | POST | 手动立即执行任务（后台异步，结果在运行历史中查看） |
| `/api/settings/automation/{task_key}/runs` | GET | 任务运行历史（最近 N 次，含失败原因） |
| `/api/notifications` | GET | 通知列表（广播 + 定向本人，含未读数） |
| `/api/notifications/unread-count` | GET | 未读角标数（轮询用轻量接口） |
| `/api/notifications/read-all` | POST | 全部已读（把本人已读水位线推到当前最大通知 id） |
| `/api/settings/notify/config` | GET / PUT | 通知配置视图 / 保存（事件开关按注册表白名单收敛） |
| `/api/settings/notify/webhook-test` | POST | 发送测试推送（不落库；url 缺省时用已保存的地址测试） |

### AI 能力

| 接口 | Method | 说明 |
| --- | --- | --- |
| `/api/ai/config` | GET / PUT | 智能分类/AI 配置（**多供应商**；密钥只回显掩码；保存仅管理员：Key 为应用级共享） |
| `/api/ai/test` | POST | 测试 AI 连通性（仅管理员） |
| `/api/ai/classify` | POST | AI 重新归类当前账号存量流水 |
| `/api/ai/report/generate` | POST | AI 生成周期消费分析报告（月/季/半年/年，生成预览不落库） |
| `/api/ai/report/archive` | POST | 归档报告（按周期唯一键覆盖旧版本） |
| `/api/ai/report/list` | GET | 归档报告列表（当前账号） |
| `/api/ai/report/{report_id}` | GET / DELETE | 归档报告详情（含 Markdown 正文）/ 删除 |
| `/api/ai/report` | POST | 生成月度消费分析报告（旧接口，兼容保留） |
| `/api/coach/chat` | POST | AI 财务教练对话（基于结构化摘要，**不外传原始流水**） |
| `/api/nl-query` | POST | 一句话查账（自然语言 → 结构化查询，规则优先 + LLM 白名单兜底） |
| `/api/settings/learned-rules` | GET | 分类学习规则列表 |
| `/api/settings/learned-rules/{rule_id}` | PUT / DELETE | 编辑规则（改目标分类 / 启停）/ 删除规则（仅管理员） |

### 开放接口（Token 与 MCP）

| 接口 | Method | 说明 |
| --- | --- | --- |
| `/api/tokens` | GET / POST | 我的 Token 列表（不含明文）/ 签发只读 Token（明文仅此一次返回） |
| `/api/tokens/{token_id}` | DELETE | 撤销 Token（立即失效） |
| `/api/mcp` | POST | MCP Streamable HTTP（JSON-RPC 2.0：initialize / tools/list / tools/call），只读工具集 |

### 审计、备份与维护

| 接口 | Method | 说明 |
| --- | --- | --- |
| `/api/audit` | GET | 操作审计查询（管理员看全部，普通账号仅自己的操作） |
| `/api/settings/about` | GET | 应用关于信息（名称/版本/作者/仓库/宿主主题透传） |
| `/api/settings/database` | GET | 当前数据库信息（含当前账号与认领状态） |
| `/api/settings/database/test` | POST | 测试目标数据库连接 |
| `/api/settings/database/migrate` | POST | 迁移数据库并切换（免重装） |
| `/api/settings/user/claim` | POST | 认领历史无归属数据到当前账号 |
| `/api/settings/logs` | GET | 运行日志尾部（最近 N 行，仅管理员） |
| `/api/settings/logs/download` | GET | 下载完整运行日志文件（仅管理员） |
| `/api/settings/backup` | GET | 下载全量数据备份（JSON，含全部账号，仅管理员） |
| `/api/settings/restore` | POST | 从备份恢复（默认合并，`replace=true` 覆盖，仅管理员；上传上限 10MB） |
| `/api/remote-backup/config` | GET / PUT | 异地备份配置（密码不回传 / 加密存储，仅管理员） |
| `/api/remote-backup/push` | POST | 推送当前全量备份到异地（WebDAV，仅管理员） |
| `/api/update/check` | GET | 检查应用更新（比对项目 Release 与本机版本；`refresh=true` 绕过后端 5 分钟缓存） |
| `/` | GET | 前端首页 |

## 🔬 单元测试（开发期）

后端自带 pytest 测试套件（`tests/`，**78 个测试文件、821 个用例**），覆盖金额归一化、关键词归类与分类自学习、AI 能力（多供应商配置与回退、分类、周期报告、财务教练、NL 查询，外部请求全部 mock）、账单解析器（微信 xlsx / 支付宝 GBK csv / 京东 csv / 云闪付 csv）、DAO、服务层校验、批量操作与回收站、预算与共享预算、资产快照、储蓄目标与健康评分、账本维度、家庭空间、报销垫付、借贷台账、操作审计、统计接口（热力图 / 年度对比 / 消费地图）、现金流预测与预算建议、支出结构拆分、通知中心与异常自检、备份恢复 / 异地备份 / 隐私加密、跨库搬移、权限中间件与开放 API Token 权限面、MCP 端点、schema 迁移（v1→v15）以及全部 API 路由（含飞牛账号隔离）。测试使用临时 SQLite 库，不会触碰 `.local_data` 中的真实数据。

一键运行所有测试：

```bash
# Windows：双击运行
scripts\run_tests.bat

# Windows / Linux / macOS 通用：
bash scripts/run_tests.sh
# 指定 Python：
PYTHON=/path/to/python bash scripts/run_tests.sh
```

脚本自动选择 Python（`$PYTHON` > 项目 `app/venv` > 系统 `python3`/`python`），测试依赖缺失时自动 pip 安装。

**测试门禁**：`build_fpk.sh` 打包前、Gitee Go 流水线（`.workflow/build-fpk.yml` → `scripts/ci_build.sh`）构建前都会先跑完整测试套件，任何用例失败即中止构建/打包，保证只发布测试通过的版本。

**代码格式化**：Python 代码统一用 black 格式化（配置见 `pyproject.toml`）：

```bash
# 安装：app\venv\Scripts\python.exe -m pip install black（或 pip install black）
app\venv\Scripts\python.exe -m black app tests scripts
```

## 🚀 发布流程（Gitee Go）

两条流水线共用 `scripts/ci_build.sh`，按分支区分渠道：

| 分支 | 流水线 | 交付产物 | Release |
| --- | --- | --- | --- |
| `main` | `.workflow/build-fpk.yml` | `fn-finstat-latest.fpk` + `fn-finstat-v{版本}.fpk` | 正式 |
| `dev` | `.workflow/build-fpk-dev.yml` | `fn-finstat-dev.fpk` + `fn-finstat-v{版本}-dev.{构建号}.fpk` | 预发布（测试版，标 `prerelease`） |

流程：构建（测试门禁 + 前端构建 + fnpack 打包 + 生成 MD5 校验）→ 发布到 Gitee Release。
每个 Release 附 4 个附件：渠道别名包、带版本号副本、`MD5SUMS.txt`、`releaseNode.txt`。
**Release 日志由流水线自动整理**，无需手写。

```bash
# 本地预览将要生成的 Release 日志
python3 scripts/gen_release_notes.py

# 发版时更新 CHANGELOG.md（同版本幂等，可重复运行）
python3 scripts/gen_release_notes.py --update-changelog
```

- 日志按约定式提交自动分组（新功能 / 修复 / 安全 / 重构 / CI …），只统计「上次发版至今」的增量
- 提交信息请遵循 `type(scope): 描述`，例如 `feat(import): 支持导出差异报告 CSV`
- `VERSION` 文件是应用版本号的唯一真实来源；Release 的 tag 即 `v{应用版本号}`（dev 渠道带 `-dev` 后缀）。**不要为测试版修改 `VERSION`**，那会连带影响正式发版的版本线
- 发布包完整性用附件 `MD5SUMS.txt` 校验：`md5sum -c MD5SUMS.txt`（描述正文里也写了这条指引）

完整说明（流水线结构、基线推断、tag 策略、排查清单）见 [`docs/发布流程与Release日志.md`](docs/发布流程与Release日志.md)。

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

## 👤 作者

- **作者**：[zhangyilin_233](https://gitee.com/zhangyilin_233)
- **项目地址**：<https://gitee.com/zhangyilin_233/fn-finstat>
- 应用「设置」页底部可随时查看当前版本与作者信息（`GET /api/settings/about`），并在同一张卡片里**检查更新**（`GET /api/update/check`：比对本机版本与项目 Release，给出更新说明与 fpk 下载入口）

## 📄 License

MIT © 2026 zhangyilin_233
