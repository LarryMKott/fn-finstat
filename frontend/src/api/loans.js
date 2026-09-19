/* 借贷台账接口（/api/loans，T-7.5）
 * 借出（应收）/ 借入（应付）的本金与还款跟踪；还清即结项 */
import { api } from "./client";

export const loanLedger = () => api("/api/loans");
export const createLoan = (payload) =>
  api("/api/loans", { method: "POST", body: JSON.stringify(payload) });
export const updateLoan = (id, payload) =>
  api(`/api/loans/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
export const deleteLoan = (id) =>
  api(`/api/loans/${encodeURIComponent(id)}`, { method: "DELETE" });
export const loanPayments = (id) =>
  api(`/api/loans/${encodeURIComponent(id)}/payments`);
export const addLoanPayment = (id, payload) =>
  api(`/api/loans/${encodeURIComponent(id)}/payments`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const deleteLoanPayment = (id, paymentId) =>
  api(
    `/api/loans/${encodeURIComponent(id)}/payments/${encodeURIComponent(paymentId)}`,
    { method: "DELETE" },
  );
