/* 图表生命周期组合式函数：init / resize / dispose / 主题重绘
 *
 * 统一此前 5 个图表组件各自手写的同构样板：
 *   - 首次渲染时 init（容器须可见且在文档中，避免 0 尺寸实例与孤儿节点）
 *   - 容器尺寸自适应（ResizeObserver）、组件卸载 dispose
 *   - 主题切换（isDark 变化）时以新配色重绘已存在的图表
 */
import { onBeforeUnmount, onMounted, watch } from "vue";
import echarts from "../utils/charts";
import { isDark } from "../theme";

/**
 * @param {import("vue").Ref<HTMLElement|null>} elRef 图表容器 ref
 * @param {(chart: object) => void} renderFn 渲染函数，内部读取响应式数据并 setOption
 * @param {{ canRender?: () => boolean }} [opts]
 *   canRender：主题重绘的前置条件（如面板处于激活 tab）。面板隐藏时容器为
 *   display:none，重绘得到 0 尺寸画面；传此条件可跳过（切回时会重新 load）。
 * @returns {{ render: () => object|undefined, resize: () => void, dispose: () => void, instance: object|null }}
 */
export function useChart(elRef, renderFn, { canRender = null } = {}) {
  let chart = null;
  let ro = null;

  function render() {
    const el = elRef.value;
    /* 容器须在文档中：异步回调（迟到响应、防抖重绘）触发时组件可能已卸载 */
    if (!el || !el.isConnected) return;
    /* 容器被替换（v-if 重建）：旧实例仍挂在旧 DOM 上，丢弃重建 */
    if (!chart || chart.getDom() !== el) {
      dispose();
      chart = echarts.init(el);
    }
    renderFn(chart);
    return chart;
  }

  function onResize() {
    if (chart) chart.resize();
  }

  function dispose() {
    if (chart) {
      chart.dispose();
      chart = null;
    }
  }

  watch(isDark, () => {
    if (chart && (!canRender || canRender())) render();
  });

  /* 容器元素替换（v-if 重建）：旧实例与 RO 观察目标全部失效，随换随清 */
  watch(elRef, (el) => {
    dispose();
    if (ro) {
      ro.disconnect();
      if (el) ro.observe(el);
    }
  });

  onMounted(() => {
    if (typeof ResizeObserver !== "undefined") {
      /* 优于 window resize：侧栏折叠、flex 重排等容器尺寸变化同样自适应，
       * 且各实例独立观察自己的容器，不往 window 上堆全局监听 */
      ro = new ResizeObserver(() => onResize());
      if (elRef.value) ro.observe(elRef.value);
    } else {
      window.addEventListener("resize", onResize);
    }
  });
  onBeforeUnmount(() => {
    if (ro) {
      ro.disconnect();
      ro = null;
    } else {
      window.removeEventListener("resize", onResize);
    }
    dispose();
  });

  return {
    render,
    resize: onResize,
    dispose,
    /** 当前 echarts 实例（未 init 时为 null） */
    get instance() {
      return chart;
    },
  };
}
