import { create } from "zustand";

/**
 * 功能：描述调试面板中单条前端事件日志。
 * 入参：无；这是日志记录的结构类型。
 * 出参：DebugEvent，包含 ISO 时间戳和面向开发者的中文消息。
 * 异常：类型定义不抛异常；时间戳由写入动作生成。
 */
type DebugEvent = {
  at: string;
  message: string;
};

/**
 * 功能：描述调试面板需要保留的请求、SSE 与 trace 可观测性状态。
 * 入参：无；这是 Zustand store 的状态契约类型。
 * 出参：DebugState，包含最近请求、最近 SSE 事件、trace_id 与日志动作。
 * 异常：类型定义不抛异常；未知 payload 保持 unknown，避免前端臆断后端契约。
 */
type DebugState = {
  traceId: string;
  lastRequest: unknown;
  lastSseEvent: unknown;
  logs: DebugEvent[];
  setTraceId: (traceId: string) => void;
  setLastRequest: (request: unknown) => void;
  setLastSseEvent: (evt: unknown) => void;
  addLog: (message: string) => void;
  clearLogs: () => void;
};

/**
 * 功能：提供调试面板读取和更新可观测性状态的 Zustand hook。
 * 入参：selector（由 Zustand 在调用 hook 时接收）：组件选择所需状态片段。
 * 出参：选中的调试状态或动作；日志最多保留最近 200 条，避免长会话撑爆内存。
 * 异常：不显式抛异常；写入动作不访问后端，时间生成失败以运行时异常暴露。
 */
export const useDebugStore = create<DebugState>((set) => ({
  traceId: "",
  lastRequest: null,
  lastSseEvent: null,
  logs: [],
  setTraceId: (traceId) => set({ traceId }),
  setLastRequest: (lastRequest) => set({ lastRequest }),
  setLastSseEvent: (lastSseEvent) => set({ lastSseEvent }),
  addLog: (message) =>
    set((state) => ({
      logs: [...state.logs.slice(-199), { at: new Date().toISOString(), message }],
    })),
  clearLogs: () => set({ logs: [] }),
}));
