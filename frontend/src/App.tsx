import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArchiveIcon,
  BotIcon,
  BookOpenIcon,
  BugIcon,
  ChevronDownIcon,
  ChevronLeftIcon,
  CompassIcon,
  EyeOffIcon,
  FilterIcon,
  FlaskConicalIcon,
  HeartPulseIcon,
  Link2Icon,
  LoaderCircleIcon,
  MapIcon,
  MenuIcon,
  MessageSquareTextIcon,
  PackageIcon,
  PlusCircleIcon,
  RotateCcwIcon,
  ScrollTextIcon,
  SearchIcon,
  SendIcon,
  ShieldIcon,
  SparklesIcon,
  SquareIcon,
  SwordsIcon,
  Trash2Icon,
  UploadIcon,
  UserRoundIcon,
  WandSparklesIcon,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { createSession, getSession } from "@/api/sessions";
import { listStoryPacks } from "@/api/storyPacks";
import { createTurn } from "@/api/turns";
import { commitSandbox, discardSandbox } from "@/api/sandbox";
import { getMemory, refreshMemory } from "@/api/memory";
import { resetSession } from "@/api/runtime";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useTurnStream } from "@/hooks/useTurnStream";
import { cn } from "@/lib/utils";
import { useDebugStore } from "@/stores/debugStore";
import { useStreamStore } from "@/stores/streamStore";
import { useUiStore } from "@/stores/uiStore";
import type {
  ActiveCharacter,
  CharacterStatusEffect,
  SceneObjectRef,
  SceneSnapshot,
  SessionPayload,
  StoryPackSummary,
  TurnResult,
} from "@/types";

type ChatMessage = {
  role: "system" | "player" | "gm" | "error";
  text: string;
  at: string;
  quickActions?: string[];
};

type MetricValue = {
  current: number;
  max: number;
};

type SceneQuickActionGroups = {
  current: string[];
  nearby: string[];
};

type SceneQuickActionLayout = {
  commonActions: string[];
  objectActions: Record<string, string[]>;
  diagnostics: {
    layoutFallbackUsed: boolean;
    layoutCommonCount: number;
    layoutObjectKeys: string[];
    layoutUnmappedActions: string[];
  };
};

const initialMessages: ChatMessage[] = [];
const PLAY_SESSION_STORAGE_KEY = "llmtre.app.play-session";

type PersistedAppState = {
  sessionId: string;
  characterId: string;
  userInput: string;
  sessionData: SessionPayload | null;
  turnData: TurnResult | null;
  memoryText: string;
  messages: ChatMessage[];
  selectedPackId: string;
};

function nowClock(): string {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

/**
 * 功能：把未知值安全转换为字符串，供 UI 展示后端契约里的可选字段。
 * 入参：value（unknown）：任意后端返回值；fallback（string，默认 '-'）：空值兜底文案。
 * 出参：string，适合直接渲染的文本。
 * 异常：不抛异常；无法识别的对象会降级为 fallback，避免界面渲染失败。
 */
function textValue(value: unknown, fallback = "-"): string {
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return fallback;
}

/**
 * 功能：从可能不同命名的角色字段中解析 HP/MP 数值。
 * 入参：character（Record<string, unknown> | null | undefined）：后端 active_character。
 * 出参：{ hp, mp }，字段缺失时返回 null，表示 UI 使用占位态。
 * 异常：不抛异常；非数值字段会被忽略并降级为空状态。
 */
function parseCharacterMetrics(
  character: Record<string, unknown> | null | undefined
): { hp: MetricValue | null; mp: MetricValue | null } {
  if (!character) {
    return { hp: null, mp: null };
  }
  const hpCurrent = Number(character.hp ?? character.current_hp);
  const hpMax = Number(character.max_hp ?? character.hp_max);
  const mpCurrent = Number(character.mp ?? character.current_mp);
  const mpMax = Number(character.max_mp ?? character.mp_max);
  return {
    hp:
      Number.isFinite(hpCurrent) && Number.isFinite(hpMax)
        ? { current: hpCurrent, max: hpMax }
        : null,
    mp:
      Number.isFinite(mpCurrent) && Number.isFinite(mpMax)
        ? { current: mpCurrent, max: mpMax }
        : null,
  };
}

/**
 * 功能：从后端场景快照中提取可点击行动，优先使用权威 quick_actions，再补充 affordances 与 suggested_actions。
 * 入参：turnData（TurnResult | null）：最近回合结果；scene（SceneSnapshot | null | undefined）：当前场景。
 * 出参：string[]，去重后的可提交行动文本。
 * 异常：不抛异常；缺失或禁用 affordance 会被过滤。
 */
function resolveTurnQuickActions(turn: TurnResult): string[] {
  const raw = turn.quick_actions?.length
    ? turn.quick_actions
    : (turn.affordances ?? [])
        .filter((item) => item.enabled)
        .map((item) => item.user_input || item.label);
  return [...new Set((raw ?? []).filter(Boolean) as string[])].slice(0, 10);
}

/**
 * 功能：把快捷动作文本归一化为语义键，用于“检查四周/观察周围”等同义动作去重。
 * 入参：action（string）：原始快捷动作文本。
 * 出参：string，语义去重键；无法命中规则时返回原文本去空白后的结果。
 * 异常：不抛异常；所有分支均降级为稳定字符串，避免 UI 去重抛错。
 */
function normalizeActionSemanticKey(action: string): string {
  const normalized = action.replace(/\s+/g, "").trim();
  if (!normalized) {
    return "";
  }
  // TODO(A1-quick-action-intent): 前端仅做展示兜底；后续改为消费后端 LLM 约束后的 canonical_intent_key，
  // 减少纯规则归一化导致的同义动作漏去重。
  const compact = normalized
    .replace(/一下子|一下儿|一下|一会/g, "")
    .trim();
  if (
    /(检查|观察|查看|看看|环顾|打量|侦查|探查|巡视).*(周围|四周|附近|这里|周遭)/.test(
      compact
    )
  ) {
    return "inspect-surroundings";
  }
  return compact;
}

/**
 * 功能：从场景快照构造“当前场景/临近场景”两组快捷动作，并做语义级去重。
 * 入参：scene（SceneSnapshot | null）：当前场景快照。
 * 出参：SceneQuickActionGroups，按场景分组后的快捷动作集合。
 * 异常：不抛异常；缺失 affordances 时回退到 suggested_actions 与 available_actions。
 */
function resolveSceneQuickActions(
  turnData: TurnResult | null,
  scene: SceneSnapshot | null
): SceneQuickActionGroups {
  if (!scene) {
    const rawGroups = turnData?.quick_action_groups;
    return {
      current: Array.isArray(rawGroups?.current) ? rawGroups.current : [],
      nearby: Array.isArray(rawGroups?.nearby) ? rawGroups.nearby : [],
    };
  }
  const backendGroups = turnData?.quick_action_groups;
  const currentFromBackend = Array.isArray(backendGroups?.current)
    ? backendGroups.current
    : [];
  const nearbyFromBackend = Array.isArray(backendGroups?.nearby)
    ? backendGroups.nearby
    : [];
  if (currentFromBackend.length || nearbyFromBackend.length) {
    return {
      current: [...new Set(currentFromBackend)].slice(0, 10),
      nearby: [...new Set(nearbyFromBackend)].slice(0, 10),
    };
  }
  const currentLocationId = textValue(scene.current_location?.id, "");
  const buckets: SceneQuickActionGroups = { current: [], nearby: [] };
  const seenCurrent = new Set<string>();
  const seenNearby = new Set<string>();

  for (const affordance of scene.affordances ?? []) {
    if (!affordance.enabled) {
      continue;
    }
    const actionText = textValue(affordance.user_input ?? affordance.label, "").trim();
    if (!actionText) {
      continue;
    }
    const semanticKey = normalizeActionSemanticKey(actionText);
    if (!semanticKey) {
      continue;
    }
    const targetLocationId = textValue(affordance.location_id, "");
    const objectId = textValue(affordance.object_id, "");
    const isNearby =
      objectId.startsWith("exit:") ||
      affordance.action_type === "move" ||
      (targetLocationId && targetLocationId !== currentLocationId);
    if (isNearby) {
      if (!seenNearby.has(semanticKey)) {
        seenNearby.add(semanticKey);
        buckets.nearby.push(actionText);
      }
      continue;
    }
    if (!seenCurrent.has(semanticKey)) {
      seenCurrent.add(semanticKey);
      buckets.current.push(actionText);
    }
  }

  for (const fallbackAction of [
    ...(scene.suggested_actions ?? []),
    ...(scene.available_actions ?? []),
  ]) {
    const actionText = textValue(fallbackAction, "").trim();
    if (!actionText) {
      continue;
    }
    const semanticKey = normalizeActionSemanticKey(actionText);
    if (!semanticKey || seenCurrent.has(semanticKey) || seenNearby.has(semanticKey)) {
      continue;
    }
    seenCurrent.add(semanticKey);
    buckets.current.push(actionText);
  }

  return {
    current: buckets.current.slice(0, 10),
    nearby: buckets.nearby.slice(0, 10),
  };
}

/**
 * 功能：生成场景快捷动作布局（顶部公共动作 + 各地点卡片动作），并执行跨区域语义去重。
 * 入参：turnData（TurnResult | null）：本回合结果；scene（SceneSnapshot | null）：场景快照。
 * 出参：SceneQuickActionLayout，供场景栏与地点卡片渲染。
 * 异常：不抛异常；字段缺失时降级为空布局。
 */
function resolveSceneQuickActionLayout(
  turnData: TurnResult | null,
  sessionData: SessionPayload | null,
  scene: SceneSnapshot | null
): SceneQuickActionLayout {
  const backendLayout = turnData?.quick_action_layout ?? sessionData?.quick_action_layout;
  const backendCommon = Array.isArray(backendLayout?.common_actions)
    ? backendLayout.common_actions.filter(Boolean)
    : [];
  const backendObjects =
    backendLayout?.object_actions && typeof backendLayout.object_actions === "object"
      ? backendLayout.object_actions
      : {};
  if (backendCommon.length || Object.keys(backendObjects).length) {
    const objectActions = Object.fromEntries(
      Object.entries(backendObjects).map(([key, value]) => [
        key,
        Array.isArray(value) ? [...new Set(value.filter(Boolean))] : [],
      ])
    );
    const layoutUnmapped = Array.isArray(backendLayout?.diagnostics?.unmapped_actions)
      ? (backendLayout?.diagnostics?.unmapped_actions as unknown[])
          .map((item) => textValue(item, ""))
          .filter(Boolean)
      : [];
    return {
      commonActions: [...new Set(backendCommon)],
      objectActions,
      diagnostics: {
        layoutFallbackUsed: false,
        layoutCommonCount: backendCommon.length,
        layoutObjectKeys: Object.keys(objectActions),
        layoutUnmappedActions: [...new Set(layoutUnmapped)],
      },
    };
  }
  const fallbackGroups = resolveSceneQuickActions(turnData, scene);
  return {
    commonActions: fallbackGroups.current,
    objectActions: {},
    diagnostics: {
      layoutFallbackUsed: true,
      layoutCommonCount: fallbackGroups.current.length,
      layoutObjectKeys: [],
      layoutUnmappedActions: fallbackGroups.current,
    },
  };
}

/**
 * 功能：从回合结果与场景提示中解析“当前状态”文案，避免把内部 outcome 代码直接显示给玩家。
 * 入参：turnData（TurnResult | null）：最近回合结果；scene（SceneSnapshot | null）：当前场景快照。
 * 出参：string，优先展示自然语言状态，缺失时返回默认占位文案。
 * 异常：不抛异常；字段缺失时按优先级降级到默认提示。
 */
function resolveSceneStatus(
  turnData: TurnResult | null,
  scene: SceneSnapshot | null
): string {
  return textValue(
    turnData?.suggested_next_step ??
      scene?.ui_hints?.status_text ??
      scene?.ui_hints?.status ??
      scene?.ui_hints?.hint,
    "等待玩家输入明确行动。"
  );
}

/**
 * 功能：优先使用 inventory_items 作为背包展示源，缺失时再降级到 inventory。
 * 入参：character（Record<string, unknown> | null）：当前角色快照。
 * 出参：unknown[]，用于背包与装备卡片渲染的条目列表。
 * 异常：不抛异常；字段类型不匹配时返回空数组。
 */
function resolveInventoryItems(
  character: ActiveCharacter | null
): unknown[] {
  if (!character) {
    return [];
  }
  const readable = character.inventory_items;
  if (Array.isArray(readable) && readable.length) {
    return readable;
  }
  return Array.isArray(character.inventory) ? (character.inventory as unknown[]) : [];
}

/**
 * 功能：从后端角色快照中读取派生状态效果，前端只展示，不自行推断规则状态。
 * 入参：character（ActiveCharacter | null）：后端 active_character 快照。
 * 出参：CharacterStatusEffect[]，字段非法时返回空数组。
 * 异常：不抛异常；非对象条目会被过滤，避免调试态脏数据影响页面。
 */
function resolveStatusEffects(character: ActiveCharacter | null): CharacterStatusEffect[] {
  const rawEffects = character?.status_effects;
  if (!Array.isArray(rawEffects)) {
    return [];
  }
  return rawEffects
    .filter((item): item is CharacterStatusEffect => Boolean(item) && typeof item === "object")
    .map((item) => ({
      key: textValue(item.key, "unknown"),
      label: textValue(item.label, "未知状态"),
      kind: textValue(item.kind, "flag"),
      severity: textValue(item.severity, "info"),
      description: textValue(item.description, ""),
    }));
}

/**
 * 功能：根据建会话后的场景快照构造首条开局叙事消息，避免向玩家暴露系统提示文案。
 * 入参：payload（SessionPayload）：创建会话接口返回的完整会话数据。
 * 出参：ChatMessage[]，有可用场景信息时返回一条 GM 文案，否则返回空数组。
 * 异常：不抛异常；字段缺失时降级为不注入开局消息，仅保留调试日志。
 */
function buildOpeningMessages(payload: SessionPayload): ChatMessage[] {
  const location = payload.scene_snapshot?.current_location;
  const title = textValue(location?.name ?? location?.label, "");
  const description = textValue(
    location?.description ?? payload.scene_snapshot?.ui_hints?.description,
    ""
  );
  const parts = [title, description].filter(Boolean);
  if (!parts.length) {
    return [];
  }
  return [{ role: "gm", text: parts.join("："), at: nowClock() }];
}

type DebugTraceStage = {
  stage: string;
  status: string;
  at: string;
};

/**
 * 功能：从 TurnResult.trace/debug_trace 中提取后端真实阶段，供调试面板展示。
 * 入参：turnData（TurnResult | null）：最近回合结果。
 * 出参：DebugTraceStage[]，仅包含后端返回的阶段名、状态与时间。
 * 异常：不抛异常；字段缺失或结构异常时返回空数组，避免伪造指标。
 */
function resolveTraceStages(turnData: TurnResult | null): DebugTraceStage[] {
  const trace = turnData?.trace;
  const traceObject =
    trace && typeof trace === "object" ? (trace as Record<string, unknown>) : null;
  const rawStages = Array.isArray(traceObject?.stages)
    ? traceObject.stages
    : Array.isArray(turnData?.debug_trace)
      ? turnData.debug_trace
      : [];
  return rawStages
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item) => ({
      stage: textValue(item.stage ?? item.name ?? item.event, "unknown"),
      status: textValue(item.status, "unknown"),
      at: textValue(item.at ?? item.time ?? item.timestamp, ""),
    }));
}

/**
 * 功能：统计 TurnResult 与 SSE 终止事件中的错误数量，供调试状态展示。
 * 入参：turnData（TurnResult | null）：最近回合结果；lastSseEvent（unknown）：最近 SSE 事件。
 * 出参：number，已知错误数量。
 * 异常：不抛异常；未知结构按 0 处理。
 */
function resolveDebugErrorCount(
  turnData: TurnResult | null,
  lastSseEvent: unknown
): number {
  const trace = turnData?.trace;
  const traceObject =
    trace && typeof trace === "object" ? (trace as Record<string, unknown>) : null;
  const traceErrors = Array.isArray(traceObject?.errors) ? traceObject.errors.length : 0;
  const resultErrors = Array.isArray(turnData?.errors) ? turnData.errors.length : 0;
  const sseObject =
    lastSseEvent && typeof lastSseEvent === "object"
      ? (lastSseEvent as Record<string, unknown>)
      : null;
  const sseIsError = sseObject?.event === "error" ? 1 : 0;
  return traceErrors + resultErrors + sseIsError;
}

/**
 * 功能：根据 trace 阶段时间计算真实耗时；时间缺失时明确显示未记录。
 * 入参：stages（DebugTraceStage[]）：后端 trace 阶段。
 * 出参：string，可直接显示的耗时文本。
 * 异常：不抛异常；无法解析日期时返回“未记录”。
 */
function formatTraceDuration(stages: DebugTraceStage[]): string {
  const timestamps = stages
    .map((stage) => Date.parse(stage.at))
    .filter((value) => Number.isFinite(value));
  if (timestamps.length < 2) {
    return "未记录";
  }
  const durationMs = Math.max(...timestamps) - Math.min(...timestamps);
  return durationMs < 1000 ? `${durationMs}ms` : `${(durationMs / 1000).toFixed(2)}s`;
}

/**
 * 功能：计算相邻 trace 阶段之间的真实间隔，缺失时显示未记录。
 * 入参：currentAt（string）：当前阶段时间；previousAt（string | undefined）：上一阶段时间。
 * 出参：string，阶段间隔文本。
 * 异常：不抛异常；无法解析日期时返回“未记录”。
 */
function formatStageDelta(currentAt: string, previousAt?: string): string {
  const current = Date.parse(currentAt);
  const previous = previousAt ? Date.parse(previousAt) : NaN;
  if (!Number.isFinite(current) || !Number.isFinite(previous)) {
    return "未记录";
  }
  const deltaMs = Math.max(0, current - previous);
  return deltaMs < 1000 ? `+${deltaMs}ms` : `+${(deltaMs / 1000).toFixed(2)}s`;
}

/**
 * 功能：A1 React 可玩页面，复用后端 A1 API 并以 shadcn/ui 组件组织游戏交互。
 * 入参：无。
 * 出参：JSX.Element。
 * 异常：组件内部捕获接口错误并写入消息流与调试日志，不让异常中断页面。
 */
export function App() {
  const [sessionId, setSessionId] = useState("");
  const [characterId, setCharacterId] = useState("player_01");
  const [userInput, setUserInput] = useState("");
  const [sessionData, setSessionData] = useState<SessionPayload | null>(null);
  const [turnData, setTurnData] = useState<TurnResult | null>(null);
  const [memoryText, setMemoryText] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [selectedPackId, setSelectedPackId] = useState("");
  const [didHydrate, setDidHydrate] = useState(false);
  const [lastBackendPayload, setLastBackendPayload] = useState<unknown>(null);

  const outputMode = useUiStore((s) => s.outputMode);
  const setOutputMode = useUiStore((s) => s.setOutputMode);
  const debugVisible = useUiStore((s) => s.debugVisible);
  const toggleDebug = useUiStore((s) => s.toggleDebug);
  const isBusy = useStreamStore((s) => s.isBusy);
  const streamingText = useStreamStore((s) => s.streamingText);
  const addLog = useDebugStore((s) => s.addLog);
  const traceId = useDebugStore((s) => s.traceId);
  const lastRequest = useDebugStore((s) => s.lastRequest);
  const lastSseEvent = useDebugStore((s) => s.lastSseEvent);
  const logs = useDebugStore((s) => s.logs);

  const stream = useTurnStream();
  const storyPacksQuery = useQuery({
    queryKey: ["story-packs"],
    queryFn: listStoryPacks,
    staleTime: 30_000,
  });
  const scene = turnData?.scene_snapshot ?? sessionData?.scene_snapshot ?? null;
  const activeCharacter: ActiveCharacter | null =
    turnData?.active_character ?? sessionData?.active_character ?? null;
  const sessionCharacterId = textValue(
    sessionData?.character_id ?? activeCharacter?.id ?? activeCharacter?.character_id,
    ""
  );
  const hasSession = Boolean(sessionData?.session_id);
  const metrics = parseCharacterMetrics(activeCharacter);
  const inventory = useMemo(
    () => resolveInventoryItems(activeCharacter),
    [activeCharacter]
  );
  const quests = scene?.active_quests ?? [];
  const sceneQuickActionLayout = useMemo(
    () => resolveSceneQuickActionLayout(turnData, sessionData, scene),
    [turnData, sessionData, scene]
  );
  const displayedMemory =
    memoryText ||
    turnData?.memory_summary ||
    sessionData?.memory_summary ||
    scene?.recent_memory ||
    "";
  const sessionTurn =
    turnData?.session_turn_id ?? sessionData?.current_session_turn_id ?? 0;
  const storyPacks = storyPacksQuery.data?.packs ?? [];
  const selectedPack = storyPacks.find((pack) => pack.pack_id === selectedPackId) ?? null;

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(PLAY_SESSION_STORAGE_KEY);
      if (!raw) {
        setDidHydrate(true);
        return;
      }
      const parsed = JSON.parse(raw) as Partial<PersistedAppState>;
      setSessionId(typeof parsed.sessionId === "string" ? parsed.sessionId : "");
      setCharacterId(
        typeof parsed.characterId === "string" && parsed.characterId
          ? parsed.characterId
          : "player_01"
      );
      setUserInput(typeof parsed.userInput === "string" ? parsed.userInput : "");
      setSessionData((parsed.sessionData as SessionPayload | null) ?? null);
      setTurnData((parsed.turnData as TurnResult | null) ?? null);
      setMemoryText(typeof parsed.memoryText === "string" ? parsed.memoryText : "");
      setSelectedPackId(typeof parsed.selectedPackId === "string" ? parsed.selectedPackId : "");
      setMessages(Array.isArray(parsed.messages) ? (parsed.messages as ChatMessage[]) : initialMessages);
    } catch {
      sessionStorage.removeItem(PLAY_SESSION_STORAGE_KEY);
    } finally {
      setDidHydrate(true);
    }
  }, []);

  useEffect(() => {
    if (!didHydrate) {
      return;
    }
    const payload: PersistedAppState = {
      sessionId,
      characterId,
      userInput,
      sessionData,
      turnData,
      memoryText,
      messages,
      selectedPackId,
    };
    sessionStorage.setItem(PLAY_SESSION_STORAGE_KEY, JSON.stringify(payload));
  }, [
    didHydrate,
    sessionId,
    characterId,
    userInput,
    sessionData,
    turnData,
    memoryText,
    messages,
    selectedPackId,
  ]);

  const createSessionMutation = useMutation({
    mutationFn: async () =>
      createSession({
        character_id: characterId || undefined,
        sandbox_mode: false,
        pack_id: selectedPackId || undefined,
        scenario_id: selectedPackId ? selectedPack?.scenario_id ?? "default" : undefined,
      }),
    onSuccess: (payload) => {
      setSessionId(payload.session_id);
      if (payload.character_id) {
        setCharacterId(payload.character_id);
      }
      setSelectedPackId(payload.pack_id ?? selectedPackId);
      setSessionData(payload);
      setTurnData(null);
      setMemoryText(payload.memory_summary ?? "");
      setMessages(buildOpeningMessages(payload));
      setLastBackendPayload(payload);
      addLog(
        payload.pack_id
          ? `已创建会话: ${payload.session_id} / pack=${payload.pack_id}`
          : `已创建会话: ${payload.session_id}`
      );
    },
    onError: (err) => appendError(err),
  });

  const loadSessionMutation = useMutation({
    mutationFn: async () => getSession(sessionId),
    onSuccess: (payload) => {
      setSessionData(payload);
      if (payload.character_id) {
        setCharacterId(payload.character_id);
      }
      setSelectedPackId(payload.pack_id ?? "");
      setTurnData(null);
      setMemoryText(payload.memory_summary ?? "");
      setLastBackendPayload(payload);
      addLog(`已加载会话: ${sessionId}`);
    },
    onError: (err) => appendError(err),
  });

  const memoryMutation = useMutation({
    mutationFn: async () => getMemory(sessionId),
    onSuccess: (payload) => {
      setMemoryText(payload.summary ?? payload.text ?? "");
      addLog("记忆读取完成");
    },
    onError: (err) => appendError(err),
  });

  const refreshMemoryMutation = useMutation({
    mutationFn: async () => refreshMemory(sessionId),
    onSuccess: (payload) => {
      setMemoryText(payload.summary ?? payload.text ?? "");
      addLog("记忆刷新完成");
    },
    onError: (err) => appendError(err),
  });

  const resetMutation = useMutation({
    mutationFn: async () => resetSession(sessionId, true),
    onSuccess: (payload) => {
      setSessionData(payload);
      setTurnData(null);
      setMessages(initialMessages);
      setMemoryText("");
      setLastBackendPayload(payload);
      addLog("会话已重置");
    },
    onError: (err) => appendError(err),
  });

  const commitMutation = useMutation({
    mutationFn: async () => commitSandbox(sessionId),
    onSuccess: () => addLog("沙盒并入成功"),
    onError: (err) => appendError(err),
  });

  const discardMutation = useMutation({
    mutationFn: async () => discardSandbox(sessionId),
    onSuccess: () => addLog("沙盒回滚成功"),
    onError: (err) => appendError(err),
  });

  /**
   * 功能：把接口错误追加到对话流并记录调试日志。
   * 入参：err（unknown）：任意异常对象。
   * 出参：void。
   * 异常：不抛异常；异常内容统一转字符串降级展示。
   */
  function appendError(err: unknown): void {
    const text = String(err);
    setMessages((prev) => [...prev, { role: "error", text, at: nowClock() }]);
    addLog(`操作失败: ${text}`);
  }

  /**
   * 功能：提交玩家行动，按当前输出模式选择普通回合或 SSE 流式回合。
   * 入参：text（string）：玩家输入或快捷行动文本。
   * 出参：Promise<void>。
   * 异常：接口异常会被捕获并写入 UI；取消流式输出由 hook 处理 busy 状态。
   */
  async function submitTurn(text: string): Promise<void> {
    if (!sessionId) {
      appendError("请先创建或加载会话。");
      return;
    }
    const finalText = text.trim();
    if (!finalText) {
      return;
    }
    setUserInput("");
    setMessages((prev) => [
      ...prev,
      { role: "player", text: finalText, at: nowClock() },
    ]);
    addLog(`提交回合: ${outputMode}`);
    try {
      const result =
        outputMode === "stream"
          ? await stream.run(sessionId, {
              user_input: finalText,
              character_id: characterId || undefined,
              sandbox_mode: false,
            })
          : await createTurn(sessionId, {
              user_input: finalText,
              character_id: characterId || undefined,
              sandbox_mode: false,
            });
      setTurnData(result);
      const turnQuickActions = resolveTurnQuickActions(result);
      setLastBackendPayload(result);
      setMessages((prev) => [
        ...prev,
        {
          role: "gm",
          text: result.final_response,
          at: nowClock(),
          quickActions: turnQuickActions,
        },
      ]);
      if (result.memory_summary) {
        setMemoryText(result.memory_summary);
      }
      setSessionData((prev) =>
        prev
          ? {
              ...prev,
              current_session_turn_id: result.session_turn_id,
              scene_snapshot: result.scene_snapshot ?? prev.scene_snapshot,
              active_character: result.active_character ?? prev.active_character,
              memory_summary: result.memory_summary ?? prev.memory_summary,
            }
          : prev
      );
      addLog(
        `回合完成: s_turn=${result.session_turn_id}, r_turn=${result.runtime_turn_id}`
      );
    } catch (err) {
      appendError(err);
    }
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-40 border-b border-primary/20 bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1760px] flex-col gap-3 px-4 py-3 lg:flex-row lg:items-center">
          <div className="flex items-center gap-3">
            <div className="tre-logo flex size-14 items-center justify-center rounded-lg border border-primary/40 bg-primary/10">
              <SparklesIcon data-icon="inline-start" />
            </div>
            <div>
              <h1 className="text-3xl font-semibold text-primary">LLM TRE - A1</h1>
              <p className="text-sm text-muted-foreground">
                文本冒险 · 回合制 TRPG · 等待 trace
              </p>
            </div>
          </div>
          <div className="grid flex-1 gap-2 md:grid-cols-[minmax(170px,1fr)_minmax(220px,1.2fr)_minmax(220px,1.2fr)_auto_auto_auto_auto_auto]">
            <div className="relative">
              <UserRoundIcon data-icon="inline-start" className="pointer-events-none absolute top-2.5 left-2.5 text-muted-foreground" />
              <Input className="pl-9" value={characterId} onChange={(e) => setCharacterId(e.target.value)} />
            </div>
            <div className="relative">
              <Link2Icon data-icon="inline-start" className="pointer-events-none absolute top-2.5 left-2.5 text-muted-foreground" />
              <Input className="pl-9" value={sessionId} onChange={(e) => setSessionId(e.target.value)} placeholder="sess_xxx" />
            </div>
            <div className="relative">
              <PackageIcon data-icon="inline-start" className="pointer-events-none absolute top-2.5 left-2.5 text-muted-foreground" />
              <select
                className="h-10 w-full rounded-md border border-input bg-background px-9 text-sm shadow-sm outline-none transition-colors focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                value={selectedPackId}
                onChange={(event) => setSelectedPackId(event.target.value)}
                disabled={isBusy || storyPacksQuery.isLoading}
              >
                <option value="">默认剧本</option>
                {storyPacks.map((pack: StoryPackSummary) => (
                  <option key={pack.pack_id} value={pack.pack_id}>
                    {pack.title} · {pack.version}
                  </option>
                ))}
              </select>
            </div>
            <Button onClick={() => createSessionMutation.mutate()} disabled={isBusy || createSessionMutation.isPending}>
              {createSessionMutation.isPending ? <LoaderCircleIcon data-icon="inline-start" className="animate-spin" /> : <PlusCircleIcon data-icon="inline-start" />}
              新会话
            </Button>
            <Button variant="secondary" disabled={!sessionId || isBusy}>
              <UploadIcon data-icon="inline-start" />
              导出会话
            </Button>
            <Button variant="outline" onClick={() => loadSessionMutation.mutate()} disabled={!sessionId || isBusy}>
              <ArchiveIcon data-icon="inline-start" />
              加载
            </Button>
            <Button variant="outline" onClick={() => resetMutation.mutate()} disabled={!sessionId || isBusy}>
              <RotateCcwIcon data-icon="inline-start" />
              重置
            </Button>
            <Button variant={debugVisible ? "secondary" : "outline"} onClick={toggleDebug}>
              <BugIcon data-icon="inline-start" />
              控制台 / 调试
              <Badge variant="outline" className="ml-1">{Math.min(logs.length, 9)}</Badge>
              <ChevronDownIcon data-icon="inline-end" />
            </Button>
          </div>
        </div>
      </header>

      <main
        className={cn(
          "mx-auto grid max-w-[1760px] gap-4 px-4 py-4",
          debugVisible
            ? "xl:grid-cols-[minmax(0,1.35fr)_320px_460px]"
            : "xl:grid-cols-[minmax(0,1.62fr)_320px]"
        )}
      >
        <section className="flex min-w-0 flex-col gap-4">
          <ScenePanel
            scene={scene}
            turnData={turnData}
            isBusy={isBusy}
            sceneQuickActionLayout={sceneQuickActionLayout}
            onSubmit={submitTurn}
          />
          <ChatPanel
            messages={messages}
            streamingText={streamingText}
            isBusy={isBusy}
            userInput={userInput}
            outputMode={outputMode}
            onInputChange={setUserInput}
            onSubmit={submitTurn}
            onAbort={stream.abort}
            onModeChange={setOutputMode}
          />
        </section>

        <aside className="flex min-w-0 flex-col gap-4">
          <StatusPanel
            characterId={sessionCharacterId || characterId}
            activeCharacter={activeCharacter}
            metrics={metrics}
            sessionTurn={sessionTurn}
            sandboxMode={Boolean(sessionData?.sandbox_mode)}
            hasSession={hasSession}
          />
          <InventoryPanel inventory={inventory} />
          <QuestPanel quests={quests} />
          <MemoryPanel
            memoryText={displayedMemory}
            disabled={!sessionId}
            onRead={() => memoryMutation.mutate()}
            onRefresh={() => refreshMemoryMutation.mutate()}
            onCommit={() => commitMutation.mutate()}
            onDiscard={() => discardMutation.mutate()}
          />
        </aside>

        {debugVisible ? (
          <aside className="hidden min-w-0 xl:block">
            <DebugPanel
              lastRequest={lastRequest}
              lastSseEvent={lastSseEvent}
              logs={logs}
              turnData={turnData}
              sessionData={sessionData}
              memoryText={displayedMemory}
              backendPayload={lastBackendPayload}
              onCollapse={toggleDebug}
            />
          </aside>
        ) : (
          <aside className="hidden xl:flex items-center justify-start">
            <Button variant="ghost" size="icon" onClick={toggleDebug}>
              <ChevronLeftIcon data-icon="inline-start" />
            </Button>
          </aside>
        )}
      </main>

      <div className="fixed top-3 left-3 z-50 xl:hidden">
        <Sheet>
          <Tooltip>
            <TooltipTrigger asChild>
              <SheetTrigger asChild>
                <Button variant="outline" size="icon" aria-label="打开调试面板">
                  <MenuIcon data-icon="inline-start" />
                </Button>
              </SheetTrigger>
            </TooltipTrigger>
            <TooltipContent>调试面板</TooltipContent>
          </Tooltip>
          <DebugSheet
            lastRequest={lastRequest}
            lastSseEvent={lastSseEvent}
            logs={logs}
            turnData={turnData}
            sessionData={sessionData}
            memoryText={displayedMemory}
            backendPayload={lastBackendPayload}
          />
        </Sheet>
      </div>
    </div>
  );
}

/**
 * 功能：渲染当前场景总览，并将 NPC 与地点对象拆分为视觉独立的交互区域。
 * 入参：scene（SceneSnapshot | null）：后端场景快照；turnData（TurnResult | null）：最近回合；
 *   isBusy（boolean）：回合请求状态；sceneQuickActionLayout（SceneQuickActionLayout）：场景动作布局；
 *   onSubmit（函数）：提交快捷动作或玩家输入。
 * 出参：JSX.Element，包含位置、出口、公共快捷操作、NPC 区、地点区和状态提示。
 * 异常：不抛异常；缺失场景字段时以空列表和占位文案降级展示。
 */
function ScenePanel({
  scene,
  turnData,
  isBusy,
  sceneQuickActionLayout,
  onSubmit,
}: {
  scene: SceneSnapshot | null;
  turnData: TurnResult | null;
  isBusy: boolean;
  sceneQuickActionLayout: SceneQuickActionLayout;
  onSubmit: (value: string) => Promise<void>;
}) {
  const location = scene?.current_location ?? {};
  const sceneObjects = scene?.scene_objects ?? [];
  const npcObjects = sceneObjects.filter((item) => item.object_type === "npc");
  const placeObjects = sceneObjects.filter((item) =>
    ["location", "exit", "system"].includes(item.object_type)
  );
  const exits = scene?.exits ?? [];
  const visibleTargets = (scene?.visible_npcs ?? []).map((item) =>
    textValue(item.name ?? item.label ?? item.entity_id, "目标")
  );
  const visibleItems = (scene?.visible_items ?? []).map((item) =>
    textValue(item.name ?? item.label ?? item.item_id, "物品")
  );
  const title = textValue(location.name ?? location.label, "未进入场景");
  const description = textValue(
    location.description ?? scene?.ui_hints?.description,
    "创建或加载会话后，当前位置、出口和可见对象会显示在这里。"
  );
  const statusText = resolveSceneStatus(turnData, scene);
  return (
    <Card className="scene-stage border-primary/20 overflow-hidden">
      <CardHeader className="border-b border-primary/20">
        <CardTitle className="flex items-center gap-2 text-5xl">
          <CompassIcon data-icon="inline-start" />
          {title}
        </CardTitle>
        <CardDescription className="text-base">{description}</CardDescription>
        <CardAction>
          <Badge variant="secondary" className="bg-primary/15 text-primary">
            {isBusy ? "等待流式" : turnData ? `回合 ${turnData.session_turn_id}` : "等待会话"}
          </Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-4 pt-4 lg:grid-cols-[1fr_280px]">
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap gap-2">
            {exits.length ? (
              exits.slice(0, 6).map((exit, idx) => (
                <Badge key={`${textValue(exit.id, "exit")}-${idx}`} variant="outline">
                  <MapIcon data-icon="inline-start" />
                  {textValue(exit.name ?? exit.label ?? exit.to_location_id, "出口")}
                </Badge>
              ))
            ) : (
              <Badge variant="outline">暂无出口信息</Badge>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">
              目标：{visibleTargets.length ? visibleTargets.join("、") : "无可见目标"}
            </Badge>
            <Badge variant="outline">
              可互动物品：{visibleItems.length ? visibleItems.join("、") : "无可见物品"}
            </Badge>
          </div>
          {sceneQuickActionLayout.commonActions.length ? (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">公共快捷操作</p>
              <div className="flex flex-wrap gap-2">
                {sceneQuickActionLayout.commonActions.map((action) => (
                  <Button
                    key={`common-${action}`}
                    variant="outline"
                    size="sm"
                    disabled={isBusy}
                    onClick={() => void onSubmit(action)}
                  >
                    <WandSparklesIcon data-icon="inline-start" />
                    {action}
                  </Button>
                ))}
              </div>
            </div>
          ) : null}
          <div className="grid gap-3 xl:grid-cols-2">
            <SceneObjectSection
              title="NPC"
              emptyText="当前没有可见 NPC"
              icon={UserRoundIcon}
              items={npcObjects}
              sceneQuickActionLayout={sceneQuickActionLayout}
              isBusy={isBusy}
              onSubmit={onSubmit}
            />
            <SceneObjectSection
              title="地点"
              emptyText="当前没有地点对象"
              icon={MapIcon}
              items={placeObjects}
              sceneQuickActionLayout={sceneQuickActionLayout}
              isBusy={isBusy}
              onSubmit={onSubmit}
            />
          </div>
        </div>
        <Alert>
          <SparklesIcon data-icon="inline-start" />
          <AlertTitle>当前状态</AlertTitle>
          <AlertDescription>{statusText}</AlertDescription>
        </Alert>
      </CardContent>
    </Card>
  );
}

/**
 * 功能：按对象类型渲染一个场景交互分区，避免 NPC 与地点在同一视觉列表内混杂。
 * 入参：title（string）：分区标题；emptyText（string）：空态文案；icon（LucideIcon）：标题图标；
 *   items（SceneObjectRef[]）：本分区对象；sceneQuickActionLayout（SceneQuickActionLayout）：动作布局；
 *   isBusy（boolean）：回合请求状态；onSubmit（函数）：快捷动作提交回调。
 * 出参：JSX.Element，包含分区标题、数量、对象卡片或空态。
 * 异常：不抛异常；对象数量为 0 时展示空态，不影响其他分区渲染。
 */
function SceneObjectSection({
  title,
  emptyText,
  icon: Icon,
  items,
  sceneQuickActionLayout,
  isBusy,
  onSubmit,
}: {
  title: string;
  emptyText: string;
  icon: LucideIcon;
  items: SceneObjectRef[];
  sceneQuickActionLayout: SceneQuickActionLayout;
  isBusy: boolean;
  onSubmit: (value: string) => Promise<void>;
}) {
  return (
    <section className="min-h-[180px] rounded-lg border border-primary/20 bg-background/35 p-3">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Icon data-icon="inline-start" />
          {title}
        </h3>
        <Badge variant="secondary">{items.length}</Badge>
      </div>
      {items.length ? (
        <div className="grid gap-2">
          {items.slice(0, 4).map((item) => (
            <SceneObjectCard
              key={item.object_id}
              item={item}
              quickActions={sceneQuickActionLayout.objectActions[item.object_id] ?? []}
              isBusy={isBusy}
              onSubmit={onSubmit}
            />
          ))}
        </div>
      ) : (
        <div className="flex min-h-24 items-center rounded-md border border-dashed border-primary/20 px-3 text-sm text-muted-foreground">
          {emptyText}
        </div>
      )}
    </section>
  );
}

function SceneObjectCard({
  item,
  quickActions,
  isBusy,
  onSubmit,
}: {
  item: SceneObjectRef;
  quickActions: string[];
  isBusy: boolean;
  onSubmit: (value: string) => Promise<void>;
}) {
  return (
    <div className="rounded-lg border border-primary/20 bg-muted/30 p-3">
      <div className="flex items-start justify-between gap-2">
        <strong className="text-sm">{item.label}</strong>
        <Badge variant="outline">{item.object_type}</Badge>
      </div>
      <p className="mt-2 line-clamp-2 text-sm text-muted-foreground">
        {item.description || "可交互目标"}
      </p>
      {quickActions.length ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {quickActions.map((action) => (
            <Button
              key={`${item.object_id}-${action}`}
              size="sm"
              variant="outline"
              disabled={isBusy}
              onClick={() => void onSubmit(action)}
            >
              <WandSparklesIcon data-icon="inline-start" />
              {action}
            </Button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ChatPanel({
  messages,
  streamingText,
  isBusy,
  userInput,
  outputMode,
  onInputChange,
  onSubmit,
  onAbort,
  onModeChange,
}: {
  messages: ChatMessage[];
  streamingText: string;
  isBusy: boolean;
  userInput: string;
  outputMode: "sync" | "stream";
  onInputChange: (value: string) => void;
  onSubmit: (value: string) => Promise<void>;
  onAbort: () => void;
  onModeChange: (value: "sync" | "stream") => void;
}) {
  const hasMessages = messages.length > 1 || streamingText.length > 0;
  const modeLabel = outputMode === "stream" ? "流式" : "普通";
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const actionMessageIndex = [...messages]
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => item.role === "gm" && (item.quickActions?.length ?? 0) > 0)
    .map((item) => item.index)
    .pop();
  const latestQuickActions =
    actionMessageIndex !== undefined ? messages[actionMessageIndex]?.quickActions ?? [] : [];
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, streamingText, latestQuickActions, isBusy]);
  return (
    <Card className="border-primary/20">
      <CardHeader className="border-b border-primary/20">
        <CardTitle className="flex items-center gap-2">
          <MessageSquareTextIcon data-icon="inline-start" />
          回合记录
        </CardTitle>
        <CardDescription>输入行动或点击建议行动推进回合</CardDescription>
        <CardAction>
          <div className="flex items-center gap-2">
            <Badge variant="secondary">{modeLabel}</Badge>
            <Button variant="ghost" size="sm" disabled={!hasMessages}>
              <EyeOffIcon data-icon="inline-start" />
              清空记录
            </Button>
          </div>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 pt-4">
        <ScrollArea className="h-[360px] rounded-lg border border-primary/20 bg-muted/20">
          <div className="flex flex-col gap-3 p-3">
            {messages.map((message, idx) => (
              <MessageBubble
                key={`${message.role}-${idx}`}
                message={message}
                quickActions={idx === actionMessageIndex ? message.quickActions ?? [] : []}
                quickActionsDisabled={isBusy}
                onQuickAction={onSubmit}
              />
            ))}
            {isBusy && streamingText ? (
              <MessageBubble message={{ role: "gm", text: streamingText, at: nowClock() }} streaming />
            ) : null}
            <div ref={bottomRef} />
          </div>
        </ScrollArea>
        <Separator />
        <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
          <Textarea
            value={userInput}
            onChange={(e) => onInputChange(e.target.value)}
            placeholder="输入命令或对话，例如：观察周围"
            disabled={isBusy}
            className="h-28 resize-none border-primary/20 bg-background/40"
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing) {
                return;
              }
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void onSubmit(userInput);
              }
            }}
          />
          <div className="flex flex-col gap-2 lg:min-w-[220px]">
            <ToggleGroup
              type="single"
              value={outputMode}
              onValueChange={(value) => value && onModeChange(value as "sync" | "stream")}
              variant="outline"
            >
              <ToggleGroupItem value="stream">流式</ToggleGroupItem>
              <ToggleGroupItem value="sync">普通</ToggleGroupItem>
            </ToggleGroup>
            <p className="text-xs text-muted-foreground">
              {outputMode === "stream"
                ? "流式：逐段返回叙事，首字更快。"
                : "普通：一次性返回完整结果。"}
            </p>
            <div className="grid grid-cols-[1fr_auto] gap-2">
              <Button disabled={isBusy || !userInput.trim()} onClick={() => void onSubmit(userInput)}>
                {isBusy ? (
                  <LoaderCircleIcon data-icon="inline-start" className="animate-spin" />
                ) : (
                  <SendIcon data-icon="inline-start" />
                )}
                {isBusy ? "处理中..." : "发送"}
              </Button>
              <Button variant="outline" size="icon" disabled={isBusy || !userInput.trim()}>
                <ChevronDownIcon data-icon="inline-start" />
              </Button>
            </div>
            <Button variant="outline" disabled={!isBusy} onClick={onAbort}>
              <SquareIcon data-icon="inline-start" />
              停止
            </Button>
            <p className="text-xs text-primary/90">
              {isBusy
                ? outputMode === "stream"
                  ? "正在流式生成，请稍候或点击停止。"
                  : "正在请求完整回合结果，请稍候。"
                : "已就绪，输入行动后点击发送。"}
            </p>
            <p className="text-xs text-muted-foreground">Enter 发送，Shift + Enter 换行</p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function MessageBubble({
  message,
  streaming = false,
  quickActions = [],
  quickActionsDisabled = false,
  onQuickAction,
}: {
  message: ChatMessage;
  streaming?: boolean;
  quickActions?: string[];
  quickActionsDisabled?: boolean;
  onQuickAction?: (value: string) => Promise<void>;
}) {
  const meta =
    message.role === "player"
      ? {
          label: "玩家",
          icon: UserRoundIcon,
          className: "bg-amber-500/15 text-amber-200 border-amber-400/40",
        }
      : message.role === "gm"
        ? {
            label: "旁白",
            icon: SparklesIcon,
            className: "bg-indigo-500/15 text-indigo-200 border-indigo-400/40",
          }
        : message.role === "error"
          ? {
              label: "错误",
              icon: BugIcon,
              className: "",
            }
          : {
              label: "系统",
              icon: BotIcon,
              className: "bg-emerald-500/15 text-emerald-200 border-emerald-400/40",
            };
  const RoleIcon = meta.icon;
  return (
    <article className={cn("message-bubble", `message-${message.role}`, streaming && "animate-pulse")}>
      <div className="flex items-center justify-between">
        <Badge variant={message.role === "error" ? "destructive" : "outline"} className={meta.className}>
          <RoleIcon data-icon="inline-start" />
          {meta.label}
        </Badge>
        <span className="text-xs text-muted-foreground">{message.at}</span>
      </div>
      <p>{message.text}</p>
      {quickActions.length && onQuickAction ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {quickActions.map((action) => (
            <Button
              key={action}
              variant="outline"
              size="sm"
              disabled={quickActionsDisabled}
              onClick={() => void onQuickAction(action)}
            >
              <WandSparklesIcon data-icon="inline-start" />
              {action}
            </Button>
          ))}
        </div>
      ) : null}
    </article>
  );
}

function StatusPanel({
  characterId,
  activeCharacter,
  metrics,
  sessionTurn,
  sandboxMode,
  hasSession,
}: {
  characterId: string;
  activeCharacter: ActiveCharacter | null;
  metrics: { hp: MetricValue | null; mp: MetricValue | null };
  sessionTurn: number;
  sandboxMode: boolean;
  hasSession: boolean;
}) {
  const name = hasSession
    ? textValue(activeCharacter?.name ?? activeCharacter?.label, "旅行者")
    : "--";
  const shownCharacterId = hasSession ? characterId || "--" : "--";
  const shownTurn = hasSession ? String(sessionTurn) : "--";
  const statusSummary = hasSession
    ? textValue(activeCharacter?.status_summary, "状态稳定")
    : "--";
  const statusEffects = hasSession ? resolveStatusEffects(activeCharacter) : [];
  return (
    <Card className="border-primary/20">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <HeartPulseIcon data-icon="inline-start" />
          角色状态
        </CardTitle>
        <CardAction>
          <Badge variant={sandboxMode ? "secondary" : "outline"}>{sandboxMode ? "Shadow" : "Active"}</Badge>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <div className="flex size-14 items-center justify-center rounded-lg border bg-primary/10 font-semibold text-primary">
            {name.slice(0, 2)}
          </div>
          <div>
            <div className="font-medium">{name}</div>
            <div className="text-sm text-muted-foreground">{shownCharacterId}</div>
            <div className="text-xs text-muted-foreground">回合 {shownTurn}</div>
          </div>
        </div>
        <Metric label="HP" value={hasSession ? metrics.hp : null} />
        <Metric label="MP" value={hasSession ? metrics.mp : null} />
        <div className="rounded-lg border bg-muted/30 p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm text-muted-foreground">状态摘要</span>
            <Badge variant={statusEffects.length ? "secondary" : "outline"}>
              {statusSummary}
            </Badge>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {hasSession ? (
              statusEffects.length ? (
                statusEffects.map((effect) => (
                  <Badge key={effect.key} variant="outline" title={effect.description}>
                    {effect.label}
                  </Badge>
                ))
              ) : (
                <Badge variant="outline">状态稳定</Badge>
              )
            ) : (
              <Badge variant="outline">--</Badge>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: MetricValue | null }) {
  const percentage =
    value && value.max > 0
      ? Math.max(0, Math.min(100, (value.current / value.max) * 100))
      : 0;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-sm">
        <span>{label}</span>
        <strong>{value ? `${value.current} / ${value.max}` : "-- / --"}</strong>
      </div>
      <Progress value={percentage} />
    </div>
  );
}

function InventoryPanel({ inventory }: { inventory: unknown[] }) {
  return (
    <Card className="border-primary/20">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <PackageIcon data-icon="inline-start" />
          背包 / 装备
        </CardTitle>
        <CardAction>
          <Badge variant="outline">{inventory.length}</Badge>
        </CardAction>
      </CardHeader>
      <CardContent>
        {inventory.length ? (
          <div className="grid grid-cols-1 gap-2">
            {inventory.slice(0, 6).map((item, idx) => (
              <div key={idx} className="rounded-lg border bg-muted/30 p-2 text-sm">
                <div className="font-medium">
                  {typeof item === "object" && item
                    ? textValue(
                        (item as Record<string, unknown>).name ??
                          (item as Record<string, unknown>).item_id,
                        "物品"
                      )
                    : textValue(item, "物品")}
                </div>
                <div className="text-xs text-muted-foreground">
                  {typeof item === "object" && item
                    ? textValue(
                        (item as Record<string, unknown>).description ??
                          (item as Record<string, unknown>).item_type ??
                          (item as Record<string, unknown>).item_id,
                        "暂无物品描述。"
                      )
                    : "物品目录未命中，暂以内部 ID 显示。"}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">暂无物品信息</p>
        )}
      </CardContent>
    </Card>
  );
}

function QuestPanel({ quests }: { quests: Record<string, unknown>[] }) {
  return (
    <Card className="border-primary/20">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ScrollTextIcon data-icon="inline-start" />
          当前任务
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {quests.length ? (
          quests.slice(0, 4).map((quest, idx) => (
            <div key={idx} className="rounded-lg border bg-muted/30 p-3">
              <strong className="text-sm">
                {textValue(quest.name ?? quest.title ?? quest.quest_id, "未命名任务")}
              </strong>
              <p className="mt-1 text-sm text-muted-foreground">
                {textValue(quest.description ?? quest.status, "等待推进")}
              </p>
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground">暂无活跃任务</p>
        )}
      </CardContent>
    </Card>
  );
}

function MemoryPanel({
  memoryText,
  disabled,
  onRead,
  onRefresh,
  onCommit,
  onDiscard,
}: {
  memoryText: string;
  disabled: boolean;
  onRead: () => void;
  onRefresh: () => void;
  onCommit: () => void;
  onDiscard: () => void;
}) {
  return (
    <Card className="border-primary/20">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BookOpenIcon data-icon="inline-start" />
          记忆摘要
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="max-h-28 overflow-auto rounded-lg border bg-muted/30 p-3 text-sm text-muted-foreground">
          {memoryText || "暂无记忆摘要。"}
        </p>
        <div className="grid grid-cols-3 gap-2">
          <Button variant="secondary" disabled={disabled} onClick={onRead}>
            <BookOpenIcon data-icon="inline-start" />
            读取
          </Button>
          <Button variant="secondary" disabled={disabled} onClick={onRefresh}>
            <RotateCcwIcon data-icon="inline-start" />
            刷新
          </Button>
          <Button variant="outline" disabled={!memoryText}>
            <EyeOffIcon data-icon="inline-start" />
            清空
          </Button>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Button variant="outline" disabled={disabled} onClick={onCommit}>
            <ShieldIcon data-icon="inline-start" />
            并入
          </Button>
          <Button variant="outline" disabled={disabled} onClick={onDiscard}>
            <SwordsIcon data-icon="inline-start" />
            回滚
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function DebugSheet(props: DebugPanelProps) {
  return (
    <SheetContent className="w-[92vw] sm:max-w-xl">
      <SheetHeader>
        <SheetTitle>调试面板</SheetTitle>
        <SheetDescription>请求、SSE、Trace 与记忆快照。</SheetDescription>
      </SheetHeader>
      <div className="min-h-0 flex-1 px-4 pb-4">
        <DebugPanel {...props} compact />
      </div>
    </SheetContent>
  );
}

type DebugPanelProps = {
  lastRequest: unknown;
  lastSseEvent: unknown;
  logs: unknown[];
  turnData: TurnResult | null;
  sessionData: SessionPayload | null;
  memoryText: string;
  backendPayload: unknown;
  compact?: boolean;
  onCollapse?: () => void;
};

function DebugPanel({
  lastRequest,
  lastSseEvent,
  logs,
  turnData,
  sessionData,
  memoryText,
  backendPayload,
  compact = false,
  onCollapse,
}: DebugPanelProps) {
  const activeCharacter = turnData?.active_character ?? sessionData?.active_character ?? null;
  const traceStages = resolveTraceStages(turnData);
  const traceRows = traceStages.length
    ? traceStages.slice(-10).map((stage, index, rows) => ({
        at: stage.at || "未记录",
        title: `${stage.stage} · ${stage.status}`,
        cost: formatStageDelta(stage.at, rows[index - 1]?.at),
      }))
    : logs.slice(-10).map((entry) => ({
        at: "未记录",
        title: String(entry),
        cost: "未记录",
      }));
  const eventCount = logs.length + Number(Boolean(turnData));
  const errorCount = resolveDebugErrorCount(turnData, lastSseEvent);
  const statusValue = eventCount ? (errorCount ? `失败 ${errorCount}` : "未见错误") : "--";
  const durationValue = formatTraceDuration(traceStages);
  const tokenCount = turnData?.trace
    ? JSON.stringify(turnData.trace).length
    : JSON.stringify({ lastRequest, lastSseEvent }).length;
  return (
    <Card className={cn("h-full overflow-hidden border-primary/20", compact && "border-0 shadow-none ring-0")}>
      <CardHeader className="border-b border-primary/20 bg-card/65">
        <CardTitle className="flex items-center gap-2">
          <FlaskConicalIcon data-icon="inline-start" />
          控制台 / 调试信息
        </CardTitle>
        <CardAction>
          <Button variant="ghost" size="icon" onClick={onCollapse} disabled={!onCollapse} aria-label="收起调试面板">
            <ChevronLeftIcon data-icon="inline-start" />
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="p-0">
        <Tabs defaultValue="trace" className="flex flex-col gap-0">
          <TabsList
            variant="line"
            className="grid h-14 w-full grid-cols-5 rounded-none border-b border-primary/20 bg-card/45 p-0"
          >
            <TabsTrigger className="rounded-none text-base" value="status">状态</TabsTrigger>
            <TabsTrigger className="rounded-none text-base" value="trace">Trace</TabsTrigger>
            <TabsTrigger className="rounded-none text-base" value="logs">日志</TabsTrigger>
            <TabsTrigger className="rounded-none text-base" value="memory">内存</TabsTrigger>
            <TabsTrigger className="rounded-none text-base" value="backend">后端原始</TabsTrigger>
          </TabsList>
          <TabsContent className="m-0 p-4" value="status">
            <div className="grid grid-cols-2 gap-2">
              <StatCard label="总事件" value={String(eventCount)} />
              <StatCard label="状态" value={statusValue} />
              <StatCard label="总耗时" value={durationValue} />
              <StatCard label="Tokens" value={String(tokenCount)} />
            </div>
            <DebugPre
              value={{
                character_state_flags: activeCharacter?.state_flags ?? [],
                character_status_effects: activeCharacter?.status_effects ?? [],
                character_status_context: activeCharacter?.status_context ?? null,
                layout_common_count:
                  turnData?.quick_action_layout?.common_actions?.length ?? 0,
                layout_object_keys: Object.keys(
                  turnData?.quick_action_layout?.object_actions ?? {}
                ),
                layout_unmapped_actions:
                  turnData?.quick_action_layout?.diagnostics?.unmapped_actions ?? [],
                layout_fallback_used:
                  turnData?.quick_action_layout == null,
              }}
            />
            <DebugPre value={{ sessionData, turnData }} />
            <DebugPre value={{ stream_done_quick_actions: turnData?.quick_actions ?? [] }} />
          </TabsContent>
          <TabsContent className="m-0 p-4" value="trace">
            <div className="grid grid-cols-4 gap-3">
              <StatCard label="总事件" value={String(eventCount)} />
              <StatCard label="状态" value={statusValue} />
              <StatCard label="总耗时" value={durationValue} />
              <StatCard label="Tokens" value={String(tokenCount)} />
            </div>
            <div className="mt-4 flex gap-2">
              <div className="relative flex-1">
                <SearchIcon data-icon="inline-start" className="pointer-events-none absolute top-2.5 left-2.5 text-muted-foreground" />
                <Input className="pl-9" placeholder="搜索事件、节点或内容..." />
              </div>
              <Button variant="outline">
                <FilterIcon data-icon="inline-start" />
                筛选
              </Button>
              <Button variant="outline">
                <Trash2Icon data-icon="inline-start" />
                清空
              </Button>
            </div>
            <ScrollArea className="mt-4 h-[560px] rounded-lg border border-primary/20 bg-muted/20">
              <div className="p-3">
                {traceRows.length ? (
                  traceRows.map((row, idx) => (
                    <div key={`${row.at}-${idx}`} className="grid grid-cols-[112px_minmax(0,1fr)]">
                      <div className="relative border-r border-primary/20 py-3 pr-4">
                        <span className="absolute top-5 -right-[5px] size-2 rounded-full bg-primary shadow-[0_0_16px_hsl(var(--primary))]" />
                        <div className="flex flex-col gap-1">
                          <p className="text-sm text-muted-foreground">{row.at}</p>
                          <p className="text-sm text-primary">{row.cost}</p>
                        </div>
                      </div>
                      <div className="border-b border-primary/10 py-3 pl-5">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="truncate text-base font-semibold">{row.title}</p>
                            <p className="mt-1 text-sm text-muted-foreground">节点执行与数据回流。</p>
                            <p className="mt-2 text-xs text-muted-foreground">通道：stream　分片：{idx + 1}</p>
                          </div>
                          <div className="flex items-center gap-2">
                            <Badge variant={row.title.includes("failed") ? "destructive" : "secondary"}>
                              {row.title.includes("failed") ? "失败" : "记录"}
                            </Badge>
                            <ChevronDownIcon data-icon="inline-end" />
                          </div>
                        </div>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="rounded-lg border border-primary/15 bg-background/30 p-4 text-sm text-muted-foreground">
                    暂无 Trace 事件。创建或载入会话并提交一轮后，这里会显示请求与流式事件。
                  </div>
                )}
              </div>
            </ScrollArea>
          </TabsContent>
          <TabsContent className="m-0 p-4" value="logs">
            <DebugPre value={{ lastRequest, lastSseEvent, trace: turnData?.trace }} />
          </TabsContent>
          <TabsContent className="m-0 p-4" value="memory">
            <DebugPre
              value={{
                memoryText,
                memory_summary:
                  turnData?.memory_summary ?? sessionData?.memory_summary,
              }}
            />
          </TabsContent>
          <TabsContent className="m-0 p-4" value="backend">
            <DebugPre value={backendPayload} />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-primary/20 bg-muted/20 p-3 text-center">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold">{value}</p>
    </div>
  );
}

function DebugPre({ value }: { value: unknown }) {
  return (
    <ScrollArea className="mt-3 h-[420px] rounded-lg border border-primary/20 bg-muted/25">
      <pre className="p-3 text-xs leading-relaxed">{stringifyDebug(value)}</pre>
    </ScrollArea>
  );
}

function stringifyDebug(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return String(value);
  }
}
