import { create } from "zustand";

type OutputMode = "sync" | "stream";

/**
 * 功能：描述只属于前端界面层的控制状态，不承载后端游戏事实。
 * 入参：无；这是 Zustand store 的状态契约类型。
 * 出参：UiState，包含输出模式、调试面板显示状态与对应变更动作。
 * 异常：类型定义不抛异常；非法模式由 TypeScript 字面量类型在编译期拦截。
 */
type UiState = {
  outputMode: OutputMode;
  debugVisible: boolean;
  setOutputMode: (mode: OutputMode) => void;
  toggleDebug: () => void;
};

/**
 * 功能：提供 React 组件读取和更新 UI 临时态的 Zustand hook。
 * 入参：selector（由 Zustand 在调用 hook 时接收）：组件选择所需状态片段。
 * 出参：选中的 UI 状态或动作；默认输出模式为 stream，调试面板默认收起。
 * 异常：不显式抛异常；状态更新仅写入浏览器内存，不触发后端副作用。
 */
export const useUiStore = create<UiState>((set) => ({
  outputMode: "stream",
  debugVisible: false,
  setOutputMode: (outputMode) => set({ outputMode }),
  toggleDebug: () => set((state) => ({ debugVisible: !state.debugVisible })),
}));
