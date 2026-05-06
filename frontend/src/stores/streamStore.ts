import { create } from "zustand";

type StreamState = {
  isBusy: boolean;
  streamingText: string;
  setBusy: (busy: boolean) => void;
  setStreamingText: (text: string) => void;
  reset: () => void;
};

export const useStreamStore = create<StreamState>((set) => ({
  isBusy: false,
  streamingText: "",
  setBusy: (isBusy) => set({ isBusy }),
  setStreamingText: (streamingText) => set({ streamingText }),
  reset: () => set({ isBusy: false, streamingText: "" }),
}));
