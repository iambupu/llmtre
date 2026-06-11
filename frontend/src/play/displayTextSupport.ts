/**
 * 功能：把未知值安全转换为字符串，供 UI 展示后端契约里的可选字段。
 * 入参：value（unknown）：任意后端返回值；fallback（string，默认 '-'）：空值兜底文案。
 * 出参：string，适合直接渲染的文本。
 * 异常：不抛异常；无法识别的对象会降级为 fallback，避免界面渲染失败。
 */
export function textValue(value: unknown, fallback = "-"): string {
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return fallback;
}
