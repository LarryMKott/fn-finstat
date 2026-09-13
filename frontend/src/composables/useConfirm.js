/* 全局确认弹窗：替代原生 confirm()（不可主题化、移动端体验差、与应用内弹窗风格割裂）
 *
 * 用法：
 *   import { confirm } from "../composables/useConfirm";
 *   if (!(await confirm({ title: "清空回收站", message: "将彻底删除全部流水", danger: true }))) return;
 *
 * Promise 风格，组件层把调用函数改为 async/await 即可；App.vue 挂载 <AppConfirm />。 */
import { reactive } from "vue";

export const confirmState = reactive({
  visible: false,
  title: "",
  message: "",
  confirmText: "确认",
  cancelText: "取消",
  danger: false,
  /* 当前等待中的 resolve；打开新弹窗时若有旧弹窗未决，按取消处理避免悬挂 */
  _resolve: null,
});

function settle(result) {
  const resolve = confirmState._resolve;
  confirmState._resolve = null;
  confirmState.visible = false;
  if (resolve) resolve(result);
}

export function confirm({
  title = "确认操作",
  message = "",
  danger = false,
  confirmText = "确认",
  cancelText = "取消",
} = {}) {
  /* 已有弹窗在等待时先按"取消"结掉，保证 Promise 不悬挂 */
  if (confirmState._resolve) settle(false);
  return new Promise((resolve) => {
    confirmState._resolve = resolve;
    confirmState.title = title;
    confirmState.message = message;
    confirmState.danger = danger;
    confirmState.confirmText = confirmText;
    confirmState.cancelText = cancelText;
    confirmState.visible = true;
  });
}

export function confirmAccept() {
  settle(true);
}

export function confirmCancel() {
  settle(false);
}
