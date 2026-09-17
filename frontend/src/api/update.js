/* 应用更新检查接口（/api/update）
 *
 * 只读远端公开版本信息，不涉及本机数据；离线时后端以 ok=false 返回原因（HTTP 仍为 200），
 * 因此组件侧不能只靠 catch 判断「查不到」——见 resolveUpdateTone 的用法。 */
import { api, toQuery } from "./client";

/** 检查应用更新；refresh=true 忽略后端 5 分钟结果缓存，强制重新查询 */
export const checkUpdate = (refresh = false) =>
  api(`/api/update/check${toQuery({ refresh: refresh ? "true" : "" })}`, {
    /* 后端查 Gitee 的超时上限 10s，这里留余量；默认 30s 会让用户白等更久 */
    timeout: 20_000,
  });
