import { create } from "zustand";

/**
 * 功能：描述单次 SSE 回合期间的前端流式临时态。
 * 入参：无；这是 Zustand store 的状态契约类型。
 * 出参：StreamState，包含 busy 标记、累计文本和重置/写入动作。
 * 异常：类型定义不抛异常；后端流错误由调用 hook 捕获并通过动作反映到状态。
 */
type StreamState = {
  isBusy: boolean;
  streamingText: string;
  setBusy: (busy: boolean) => void;
  setStreamingText: (text: string) => void;
  reset: () => void;
};

/**
 * 功能：提供流式回合 UI 的临时状态存取，不保存权威回合结果。
 * 入参：selector（由 Zustand 在调用 hook 时接收）：组件选择所需状态片段。
 * 出参：选中的流式状态或动作；reset 会清空 busy 与累计文本。
 * 异常：不显式抛异常；状态写入仅限前端内存，后端错误由 useTurnStream 处理。
 */
export const useStreamStore = create<StreamState>((set) => ({
  isBusy: false,
  streamingText: "",
  setBusy: (isBusy) => set({ isBusy }),
  setStreamingText: (streamingText) => set({ streamingText }),
  reset: () => set({ isBusy: false, streamingText: "" }),
}));
