/* 全局轻提示：模块级响应式状态 + 触发函数 */
import { reactive } from "vue";

export const toastState = reactive({ show: false, msg: "", error: false });

let timer = null;

export function toast(msg, error = false) {
  toastState.show = true;
  toastState.msg = msg;
  toastState.error = error;
  clearTimeout(timer);
  timer = setTimeout(() => {
    toastState.show = false;
  }, 2600);
}
