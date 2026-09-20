/* AI 财务教练接口（/api/coach，T-1.6）：结构化摘要 → DeepSeek 建议，不外传原始流水 */
import { api } from "./client";

export const coachChat = (question, history = []) =>
  api("/api/coach/chat", {
    method: "POST",
    body: JSON.stringify({ question, history }),
  });
