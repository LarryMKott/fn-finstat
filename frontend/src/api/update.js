/* 应用更新检查接口（/api/update）
 *
 * 只读远端公开版本信息，不涉及本机数据；离线时后端以 ok=false 返回原因（HTTP 仍为 200），
 * 因此组件侧不能只靠 catch 判断「查不到」——见 resolveUpdateTone 的用法。 */
import { api, toQuery } from "./client";

/**
 * 检查应用更新。
 * @param {boolean} refresh true=忽略后端 5 分钟结果缓存，强制重新查询
 * @param {object}  [options]
 * @param {string}  [options.source]  发布站点：github（默认）/ gitee
 * @param {string}  [options.channel] 版本渠道：release=正式版 / dev=开发版；空=跟随本机版本
 */
export const checkUpdate = (
  refresh = false,
  { source = "github", channel = "" } = {},
) =>
  api(
    `/api/update/check${toQuery({
      refresh: refresh ? "true" : "",
      source,
      channel,
    })}`,
    {
      /* 后端查发布站点（默认 GitHub）的超时上限 10s，这里留余量；默认 30s 会让用户白等更久 */
      timeout: 20_000,
    },
  );

/**
 * 下载最新安装包（fpk）到 NAS 目录（用户授权目录，服务端解析下载地址）。
 * 安装包几十 MB 量级，慢网下可能要几分钟，超时放宽到 10 分钟。
 * @param {object} [options]
 * @param {string} [options.source]  发布站点：github（默认）/ gitee
 * @param {string} [options.channel] 版本渠道：release / dev；空=跟随本机版本
 * @returns {Promise<{ok, message, savedPath, size, md5Verified}>}
 */
export const downloadUpdateToNas = ({ source = "github", channel = "" } = {}) =>
  api("/api/update/download", {
    method: "POST",
    body: JSON.stringify({ source, channel: channel || null }),
    timeout: 600_000,
  });

/** 当前安装包下载目录配置；管理员拿完整路径，普通账号只拿目录名与 configured */
export const getDownloadDirConfig = () => api("/api/update/download-dir");

/** 保存安装包下载目录（仅管理员）；空串 = 清除配置 */
export const saveDownloadDirConfig = (download_dir) =>
  api("/api/update/download-dir", {
    method: "PUT",
    body: JSON.stringify({ download_dir }),
  });
