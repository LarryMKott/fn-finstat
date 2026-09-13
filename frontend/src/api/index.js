/* 后端接口层统一出口：按业务域分组（bill/stat/category/budget/asset/upload/settings/ai），
 * 组件按域导入（如 `import { listBills } from "../api/bill"`），也可从本文件统一导入。 */
export { api, apiUrl, toQuery, DEFAULT_TIMEOUT } from "./client";
export * from "./bill";
export * from "./stat";
export * from "./category";
export * from "./budget";
export * from "./asset";
export * from "./upload";
export * from "./settings";
export * from "./automation";
export * from "./ai";
