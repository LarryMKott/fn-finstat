# MCP Server 实现（T-1.1）

| 项 | 内容 |
| --- | --- |
| 任务编号 | T-1.1（v1.0，见《2026-09-13-功能扩展开发计划》§3.4） |
| 交付日期 | 2026-09-19 |
| 层次 | 后端（JSON-RPC 2.0 端点 + 只读工具集） |
| 涉及 schema | 无变更（纯只读查询，复用既有 DAO） |
| 测试 | tests/api/test_mcp.py 8 例；全量通过；verify_migrations 27/27 |

## 1. 设计

- **零新依赖**：MCP Streamable HTTP 以 JSON-RPC 2.0 手写实现（initialize /
  tools/list / tools/call / ping 四方法），不引入 mcp SDK——NAS 安装路径对
  新增依赖最敏感，工具型服务手写协议成本可控（GET /mcp 无服务端流，405）。
- **鉴权与全应用同链**：deps.get_identity（网关头优先 + API Token 兜底）；
  fnOS 模式下无 Token 请求被权限中间件 401（/api/mcp 非管理面），
  本地/独立部署无头 = 单机用户。
- **只读**：工具集全部为查询，user_id 由身份链强制注入（与其他接口同一
  隔离策略）；「写操作需显式开关」预留为 config.MCP_WRITE_ENABLED（默认
  关闭，当前无写工具）。
- config.MCP_ENABLED=0 时端点整体 404（收窄攻击面）。

## 2. 工具集

| 工具 | 说明 |
| --- | --- |
| query_bills | 按时间/账户/类型/分类/商户关键词筛选，分页倒序 |
| query_summary | 收支汇总 + 分类支出 TOP |
| query_budget | 某月预算进度（复用 budget_service.overview） |
| query_savings_goals | 储蓄目标与进度（起始日以来累计净结余） |

## 3. 验证

tests/api/test_mcp.py 8 例：JSON-RPC 握手（initialize/tools/list/202 通知/
ping）、工具调用（分类筛选 / 收支汇总 / 预算进度）、未知工具 isError、
未知方法 -32601、MCP_ENABLED=0 → 404、解析错误 400。全量 768 passed + 3 skipped。
