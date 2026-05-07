import { create } from "zustand";

type OutputMode = "sync" | "stream";

type UiState = {
  outputMode: OutputMode;
  debugVisible: boolean;
  setOutputMode: (mode: OutputMode) => void;
  toggleDebug: () => void;
};

export const useUiStore = create<UiState>((set) => ({
  outputMode: "stream",
  debugVisible: false,
  setOutputMode: (outputMode) => set({ outputMode }),
  toggleDebug: () => set((state) => ({ debugVisible: !state.debugVisible })),
}));
