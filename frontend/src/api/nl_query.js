/* 自然语言查账接口（/api/nl-query）：一句话问题 + 最近 3 轮追问上下文 */
import { api } from "./client";

export const askQuestion = (question, history = []) =>
  api("/api/nl-query", {
    method: "POST",
    body: JSON.stringify({ question, history }),
  });
