# tests/ 目录结构与维护约定

测试按**被测对象所属的 app 分层**归类，与 `app/` 目录一一对应——改了哪一层，
测试就在哪一层同名子目录里找。

```
tests/
├── conftest.py              # 全局夹具（db / client 等，对所有子目录自动生效）
├── verify_sdk_integration.py  # 手工核验脚本（非 pytest 用例，路径被文档引用，勿移）
├── verify_theme_parity.py
├── api/         # 路由层：client 夹具驱动的接口语义、权限、集成
├── services/    # 服务层：业务逻辑（含 AI / 调度 / 通知 / 备份等服务）
├── db/          # DAO 与数据库基础设施（engine / base / config）
├── migrations/  # schema 迁移（test_migration_vN，新增迁移同规则命名）
├── parsers/     # 账单解析器（微信 / 支付宝 / 京东 / 云闪付）
├── core/        # 中间件、权限、宿主集成（对应 app/core/）
├── utils/       # 纯工具函数（对应 app/utils/）
└── platform/    # 打包 / 发布脚本 / 前端产物等工程门禁（被测对象在 scripts/ 与 frontend/）
```

## 归类规则

- **看被测对象**：测 `app/services/xxx.py` → `services/`；测 `app/api/xxx.py` 的
  HTTP 行为（用 `client` 夹具发请求）→ `api/`；被测对象在 `scripts/`、`frontend/`
  → `platform/`。
- **混合文件按主导夹具**：一个功能文件常既有服务级断言又有接口级断言（如
  `test_ledger.py`、`test_notify.py`），按测试用例的主要驱动方式归类
  （`db` 夹具为主 → `services/`，`client` 夹具为主 → `api/`）。
- **文件名全库唯一**：pytest prepend 导入模式下测试模块按文件名全局注册，
  不同子目录不要出现同名 `test_*.py`。

## 跨文件复用

测试之间可以互相导入常量/构造函数（如 `tests.api.test_nas` 的 `write_csv`），
写全路径：`from tests.api.test_nas import write_csv`。只允许导入「数据工厂 /
常量」，不允许导入别的测试的用例函数。

## 运行

```bash
python -m pytest                      # 全量（testpaths=tests，见 pytest.ini）
python -m pytest tests/services -q    # 某一层
python -m pytest tests/db/test_bill_dao.py -q   # 单个文件
```

打包 / CI 门禁统一走 `scripts/run_tests.sh`（`python -m pytest`），与目录结构无关。
