import type { LucideIcon } from "lucide-react";

export type SceneQuickActionGroups = {
  current: string[];
  nearby: string[];
};

export type SceneQuickActionLayout = {
  commonActions: string[];
  objectActions: Record<string, string[]>;
  diagnostics: {
    layoutFallbackUsed: boolean;
    layoutCommonCount: number;
    layoutObjectKeys: string[];
    layoutUnmappedActions: string[];
  };
};

export type ActionCategoryKey =
  | "observe"
  | "inspect"
  | "talk"
  | "move"
  | "use_item"
  | "attack"
  | "other";

export type ActionCategoryGroup = {
  key: ActionCategoryKey;
  label: string;
  icon: LucideIcon;
  actions: ActionButtonChoice[];
};

export type ActionButtonChoice = {
  value: string;
  label: string;
};
