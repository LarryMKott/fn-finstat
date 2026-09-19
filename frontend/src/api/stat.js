/* 统计报表接口（/api/stat） */
import { api, toQuery } from "./client";

export const statSummary = (params = {}) => api(`/api/stat/summary${toQuery(params)}`);
export const monthTrend = (params = {}) => api(`/api/stat/month_trend${toQuery(params)}`);
export const categoryPie = (params = {}) => api(`/api/stat/category_pie${toQuery(params)}`);
export const merchantTop = (params = {}) => api(`/api/stat/merchant_top${toQuery(params)}`);
export const dailyHeatmap = (params = {}) => api(`/api/stat/daily_heatmap${toQuery(params)}`);
export const yearComparison = (params = {}) => api(`/api/stat/year_comparison${toQuery(params)}`);
export const regionMap = (params = {}) => api(`/api/stat/region_map${toQuery(params)}`);

/* 财务健康评分（T-1.3）：储蓄率 / 负债率 / 应急金月数，口径随响应公开 */
export const statHealth = () => api("/api/stat/health");
