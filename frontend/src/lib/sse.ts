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
  const combined = `${buffer}${chunk}`;
  const blocks = combined.split("\n\n");
  const remaining = blocks.pop() ?? "";
  const events: SseEvent[] = [];

  for (const block of blocks) {
    const lines = block.split("\n");
    let eventName = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trim());
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
