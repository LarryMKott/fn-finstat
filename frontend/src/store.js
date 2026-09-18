/* 共享状态：当前标签页 + 分类列表（筛选栏 / 弹窗下拉 / 分类管理页共用） */
import { ref, reactive } from "vue";
import { listCategories } from "./api/category";
import { runTask } from "./composables/useLoading";

export const store = reactive({ tab: "dashboard" });

export const categories = ref([]);

/* 问账「存为筛选」→ 流水页的筛选交接（T-6.2）：QueryPanel 写入后切到流水页，
 * BillsPanel 激活时取走（读后清空），按问账口径复现筛选 */
export const billsFilterHandoff = ref(null);

// latest 模式只接管 HUD 状态，不会取消在途 Promise：与 BillsPanel 等处的
// loadSeq 同理，旧响应晚归时不得覆盖新响应
let refreshSeq = 0;

/* 分类列表是全局共享的小请求，同样走统一任务通道：
 * silent —— 静默加载（无 HUD 浮层），页面初始化/切换时反复触发不产生视觉噪音；
 * 默认 rethrow=true，调用方原有的 catch 逻辑不受影响 */
export function refreshCategories() {
  return runTask({
    key: "categories:refresh",
    title: "加载分类列表",
    mode: "latest",
    silent: true,
    task: async () => {
      const seq = ++refreshSeq;
      const list = await listCategories();
      if (seq !== refreshSeq) return null;
      categories.value = list;
      return list;
    },
  });
}
