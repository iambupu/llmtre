export type SseEvent = {
  event: string;
  data: unknown;
};

/**
 * 功能：解析 SSE 文本分片，输出事件列表和未完成缓冲。
 * 入参：buffer（string）累计缓存；chunk（string）本次读取文本。
 * 出参：对象，含 events（SseEvent[]）与 remaining（string）。
 * 异常：JSON 解析失败时保留原始字符串，不抛异常阻断流式读取。
 */
export function parseSseChunk(
  buffer: string,
  chunk: string
): { events: SseEvent[]; remaining: string } {
  // 统一换行风格，兼容代理或网关把 LF 转成 CRLF 的场景。
  const combined = `${buffer}${chunk}`.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const blocks = combined.split("\n\n");
  const remaining = blocks.pop() ?? "";
  const events: SseEvent[] = [];

  for (const block of blocks) {
    const lines = block.split("\n");
    let eventName = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
      const normalizedLine = line.trimStart();
      if (normalizedLine.startsWith("event:")) {
        eventName = normalizedLine.slice(6).trim();
      } else if (normalizedLine.startsWith("data:")) {
        dataLines.push(normalizedLine.slice(5).trim());
      }
    }
    const raw = dataLines.join("\n");
    if (!raw) {
      events.push({ event: eventName, data: {} });
      continue;
    }
    try {
      events.push({ event: eventName, data: JSON.parse(raw) });
    } catch {
      events.push({ event: eventName, data: raw });
    }
  }
  return { events, remaining };
}
