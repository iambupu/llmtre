import { describe, expect, it } from "vitest";
import { parseSseChunk } from "@/lib/sse";

describe("lib/sse", () => {
  it("按事件块解析 received 与 done", () => {
    const raw =
      "event: received\n" +
      "data: {\"status\":\"ok\"}\n\n" +
      "event: done\n" +
      "data: {\"trace_id\":\"trc_x\",\"final_response\":\"完成\"}\n\n";
    const parsed = parseSseChunk("", raw);
    expect(parsed.events.length).toBe(2);
    expect(parsed.events[0].event).toBe("received");
    expect(parsed.events[1].event).toBe("done");
    const doneData = parsed.events[1].data as Record<string, unknown>;
    expect(doneData.trace_id).toBe("trc_x");
  });

  it("分片输入时保留 remaining 并在后续补全", () => {
    const first = parseSseChunk("", "event: gm_delta\ndata: {\"delta\":\"A");
    expect(first.events.length).toBe(0);
    const second = parseSseChunk(first.remaining, "\"}\n\n");
    expect(second.events.length).toBe(1);
    expect(second.events[0].event).toBe("gm_delta");
    const payload = second.events[0].data as Record<string, unknown>;
    expect(payload.delta).toBe("A");
  });
});
