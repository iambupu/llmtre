import { create } from "zustand";

type DebugEvent = {
  at: string;
  message: string;
};

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
