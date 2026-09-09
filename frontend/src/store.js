/* 共享状态：当前标签页 + 分类列表（筛选栏 / 弹窗下拉 / 分类管理页共用） */
import { ref, reactive } from "vue";
import { api } from "./api";

export const store = reactive({ tab: "dashboard" });

export const categories = ref([]);

export async function refreshCategories() {
  categories.value = await api("/api/category");
}
