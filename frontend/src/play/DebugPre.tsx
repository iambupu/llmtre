import { ScrollArea } from "@/components/ui/scroll-area";

/**
 * 功能：渲染可滚动 JSON 调试块。
 * 入参：value（unknown）：任意调试对象或原始载荷。
 * 出参：JSX.Element。
 * 异常：不抛异常；无法 JSON 序列化时由 stringifyDebug 转为字符串。
 */
export function DebugPre({ value }: { value: unknown }) {
  return (
    <ScrollArea className="mt-3 h-[420px] rounded-lg border border-primary/20 bg-muted/25">
      <pre className="p-3 text-xs leading-relaxed">{stringifyDebug(value)}</pre>
    </ScrollArea>
  );
}

/**
 * 功能：把调试对象稳定格式化为 JSON 字符串。
 * 入参：value（unknown）：任意调试对象。
 * 出参：string，优先返回缩进 JSON，失败时返回 String(value)。
 * 异常：捕获 JSON.stringify 抛出的循环引用等异常并降级为普通字符串。
 */
function stringifyDebug(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return String(value);
  }
}
