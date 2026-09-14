/* 全局任务进度（Loading HUD）——所有耗时异步任务的统一入口
 *
 * 设计目标（对应需求）：
 *   1. 解耦：业务侧只声明「任务名 + 步骤文案 + 真正干活的函数」，
 *      显示/关闭/防重入/异常收尾全部由本模块负责，组件里不再散落 busy 标志。
 *   2. 四态反馈：running（进行中）/ success（完成）/ error（失败，带原因）/ idle（隐藏）。
 *   3. 防并发：同一 key 默认拒绝重入（queue）；查询类可改用 latest（新请求接管，旧结果作废）。
 *   4. 轻量：非模态浮层，不铺遮罩、不用 position 占位，因此不会引起布局跳动。
 *
 * 用法：
 *   import { runTask, isBusy } from "../composables/useLoading";
 *
 *   const data = await runTask({
 *     key: "bills:load",
 *     title: "加载流水",
 *     detail: "正在查询第 2 页…",
 *     mode: "latest",                 // 查询类：允许新请求接管
 *     task: async (update, isCurrent) => {
 *       update("正在查询…");
 *       const res = await listBills(params);
 *       if (!isCurrent()) return null; // 已被同 key 新请求接管：旧响应作废
 *       update({ detail: `共 ${res.total} 条`, progress: 100 });
 *       return res;
 *     },
 *   });
 *
 *   // 按钮防重复点击
 *   <button :disabled="isBusy('bills:load')">
 *
 * 失败时 runTask 会把错误展示在 HUD 上，然后**原样抛出**（rethrow 默认 true），
 * 业务侧原有的 try/catch（回滚、补充提示）无需改动。
 *
 * <AppLoading /> 需要在 App.vue 中挂载一次。 */
import { reactive } from "vue";
/* 显式带 .js 后缀：让本模块能被 Node 直接 import（tests/loading.test.mjs 不走 vite） */
import { toast } from "../toast.js";

/* ---------------------------------------------------------------------------
   状态
   --------------------------------------------------------------------------- */

export const loadingState = reactive({
  visible: false, // 浮层是否可见
  phase: "idle", // idle | running | success | error
  key: "", // 当前展示的任务标识
  title: "", // 任务名称
  detail: "", // 当前步骤 / 进度文案
  progress: null, // 0-100 为确定进度，null 为不确定（走循环动画）
  error: "", // 失败原因
});

/* 每个 key 一个忙标记；用普通对象 + reactive 以便模板里 isBusy(key) 能触发更新 */
const busyMap = reactive({});
/* 每个 key 最近一次任务的序号，用于判定「是否已被同 key 的新任务接管」 */
const keySeq = reactive({});

/* 活动任务栈：同一时刻可能有多个不同 key 的任务在跑（例如切到看板时
 * 汇总/趋势/预算/热力图并发加载）。浮层只展示栈顶，且只有当栈空时才真正隐藏，
 * 否则先完成的请求会把仍在进行的任务的浮层提前关掉。 */
const active = [];

let globalSeq = 0;

/* 极快任务也不至于一闪而过：至少展示这么久再进入完成态 */
const MIN_VISIBLE = 420;
const SUCCESS_HOLD = 900;
/* 失败态停留更久（要留出读错误原因的时间），但仍有上限，避免长期悬挂 */
const ERROR_HOLD = 8000;

/**
 * 任务「主动放弃」的返回值：例如执行中途弹确认框、用户点了取消。
 * 返回它时浮层立即收起，既不显示成功也不显示失败。
 */
export const TASK_CANCELLED = Symbol("task-cancelled");

/* ---------------------------------------------------------------------------
   内部工具
   --------------------------------------------------------------------------- */

/** 展示最近发起的任务：用户刚点的操作优先反馈，它结束后再回落到更早的仍在跑的任务 */
function pickVisible() {
  return active.length ? active[active.length - 1] : null;
}

function sync() {
  const t = pickVisible();
  if (!t) {
    loadingState.visible = false;
    loadingState.phase = "idle";
    loadingState.error = "";
    return;
  }
  loadingState.visible = true;
  loadingState.phase = t.phase;
  loadingState.key = t.key;
  loadingState.title = t.title;
  loadingState.detail = t.detail;
  loadingState.progress = t.progress;
  loadingState.error = t.error || "";
}

function removeTask(seq) {
  const i = active.findIndex((t) => t.seq === seq);
  if (i < 0) return;
  const [t] = active.splice(i, 1);
  if (t.timer) clearTimeout(t.timer);
  sync();
}

function readError(err) {
  if (!err) return "发生未知错误";
  if (typeof err === "string") return err;
  return err.message || err.detail || "发生未知错误";
}

/* ---------------------------------------------------------------------------
   对外 API
   --------------------------------------------------------------------------- */

/** 指定 key 的任务是否正在执行（供按钮 :disabled 使用，响应式） */
export function isBusy(key) {
  return !!busyMap[key];
}

/** 手动关闭浮层（失败卡片上的「关闭」按钮）：只清掉已结束的任务，进行中的保留 */
export function dismissLoading() {
  for (const t of [...active]) {
    if (t.phase !== "running") removeTask(t.seq);
  }
}

/**
 * 执行一个受管任务。
 *
 * @param {object}   options
 * @param {string}   options.key         任务唯一标识（必填），同时也是防重入的粒度
 * @param {string}   options.title       任务名称
 * @param {string}  [options.detail]     开始时的步骤文案
 * @param {number}  [options.progress]   初始进度（0-100），缺省为不确定进度
 * @param {string}  [options.mode]       queue=拒绝重入（默认，适合写操作）
 *                                       latest=新任务接管（适合查询/加载）
 * @param {string|Function} [options.successText] 完成文案，可传函数接收返回值
 * @param {Function} [options.failed]  (result) => boolean：业务失败判定。
 *                                     任务正常返回但结果表示失败时（如 HTTP 200 +
 *                                     {ok:false, message}）走 error 相位而非成功，
 *                                     避免浮层弹出绿勾却展示「xx失败」的语义冲突
 * @param {number}  [options.minVisible] 最短展示时长
 * @param {number}  [options.successHold] 完成态停留时长
 * @param {number}  [options.errorHold]  失败态停留时长
 * @param {boolean} [options.silent]     true=只做防重入与异常收尾，不显示浮层
 * @param {boolean} [options.rethrow]    true（默认）=失败后原样抛出；false=只在浮层报错并 resolve(undefined)
 * @param {Function} options.task        (update, isCurrent) => Promise|any。
 *                                     isCurrent() 为 false 表示本任务已被同 key 的
 *                                     新任务接管（latest 模式），在途响应应作废，
 *                                     不得再写业务状态 —— latest 只作废浮层状态，
 *                                     不会取消已发出的 Promise
 * @returns {Promise<any>} 成功时 resolve 任务返回值；失败时按 rethrow 决定抛出或 resolve(undefined)；
 *                         被防重入拦截时 resolve(undefined)
 */
export function runTask(options) {
  const {
    key,
    title = "处理中",
    detail = "",
    progress = null,
    mode = "queue",
    successText = "已完成",
    failed = null,
    minVisible = MIN_VISIBLE,
    successHold = SUCCESS_HOLD,
    errorHold = ERROR_HOLD,
    silent = false,
    rethrow = true,
    task,
  } = options || {};

  if (!key) throw new Error("runTask: key 不能为空");
  if (typeof task !== "function") throw new Error("runTask: task 必须是函数");

  /* 防重入：写操作直接拒绝，并给出明确反馈，而不是静默无响应 */
  if (mode === "queue" && busyMap[key]) {
    toast(`「${title}」正在执行，请稍候`, false);
    return Promise.resolve(undefined);
  }

  const mySeq = ++globalSeq;
  keySeq[key] = mySeq;
  busyMap[key] = true;

  const entry = {
    seq: mySeq,
    key,
    title,
    detail,
    progress,
    phase: "running",
    error: "",
    timer: null,
  };
  if (!silent) {
    /* latest 模式下新请求接管：把同 key 的旧任务直接从栈里摘掉，
     * 否则旧请求返回时会把「新请求已完成」的反馈盖回去 */
    if (mode === "latest") {
      for (const t of [...active]) {
        if (t.key === key) removeTask(t.seq);
      }
    }
    active.push(entry);
    sync();
  }

  const startedAt = Date.now();

  /* 已被同 key 的新任务接管时，本次的所有状态写入作废（避免旧请求回来覆盖新状态） */
  const isCurrent = () => keySeq[key] === mySeq;

  const update = (payload) => {
    if (!isCurrent()) return;
    if (typeof payload === "string") {
      entry.detail = payload;
    } else if (payload && typeof payload === "object") {
      if (typeof payload.title === "string") entry.title = payload.title;
      if (typeof payload.detail === "string") entry.detail = payload.detail;
      if (payload.progress === null || typeof payload.progress === "number") {
        entry.progress = payload.progress;
      }
    }
    sync();
  };

  const finish = (phase, text, hold) => {
    if (!isCurrent()) return;
    entry.phase = phase;
    entry.detail = text;
    if (phase === "error") entry.error = text;
    if (phase !== "error") entry.progress = 100;
    sync();
    entry.timer = setTimeout(() => removeTask(entry.seq), Math.max(0, hold));
  };

  return Promise.resolve()
    .then(() => task(update, isCurrent))
    .then((result) => {
      if (result === TASK_CANCELLED) {
        if (isCurrent() && !silent) removeTask(entry.seq);
        return result;
      }
      /* 业务失败判定：任务正常返回但结果表示失败（HTTP 200 + {ok:false} 等），
       * 走 error 相位，语义与视觉保持一致 */
      if (typeof failed === "function" && failed(result)) {
        const reason = (result && (result.message || result.error)) || "操作失败";
        if (!silent) {
          entry.error = reason;
          finish("error", reason, errorHold);
        }
        if (rethrow) throw new Error(reason);
        return undefined;
      }
      if (silent) return result;
      const wait = Math.max(0, minVisible - (Date.now() - startedAt));
      const text = typeof successText === "function" ? successText(result) : successText;
      finish("success", text || "已完成", wait + successHold);
      return result;
    })
    .catch((err) => {
      const reason = readError(err);
      if (!silent) {
        entry.error = reason;
        finish("error", reason, errorHold);
      }
      /* 默认原样抛出：业务侧既有的 try/catch（回滚、补充提示）保持生效；
         rethrow=false 时由浮层单独承担报错，避免调用处漏 catch 造成未处理拒绝 */
      if (rethrow) throw err;
      return undefined;
    })
    .finally(() => {
      /* 只有仍是同 key 的最新任务时才清理忙标记，避免 mode=latest 下旧任务提前解锁 */
      if (isCurrent()) {
        busyMap[key] = false;
        delete keySeq[key];
      }
    });
}
