/* ECharts 按需注册，减小打包体积 */
import * as echarts from "echarts/core";
import { BarChart, HeatmapChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import {
  CalendarComponent,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  VisualMapComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  LineChart,
  PieChart,
  BarChart,
  HeatmapChart,
  /* 消费地图为城市气泡图：散点画在普通直角坐标系上（经纬度直接作 xy），
   * 不注册 GeoComponent，也就完全不依赖任何行政边界数据 */
  ScatterChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  VisualMapComponent,
  CalendarComponent,
  CanvasRenderer,
]);

export default echarts;
