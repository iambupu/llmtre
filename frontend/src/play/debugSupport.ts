import type { TurnResult } from "@/types";

import { textValue } from "@/play/displayTextSupport";

export type DebugTraceStage = {
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
export function resolveTraceStages(turnData: TurnResult | null): DebugTraceStage[] {
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
export function resolveDebugErrorCount(
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
export function formatTraceDuration(stages: DebugTraceStage[]): string {
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
export function formatStageDelta(currentAt: string, previousAt?: string): string {
  const current = Date.parse(currentAt);
  const previous = previousAt ? Date.parse(previousAt) : NaN;
  if (!Number.isFinite(current) || !Number.isFinite(previous)) {
    return "未记录";
  }
  const deltaMs = Math.max(0, current - previous);
  return deltaMs < 1000 ? `+${deltaMs}ms` : `+${(deltaMs / 1000).toFixed(2)}s`;
}

/**
 * 功能：集中管理可跨刷新恢复的游玩页状态，并同步到 sessionStorage。
 * 入参：无。
 * 出参：PersistedPlayState，包含状态值与对应 setter，供 App 编排 API 与布局。
 * 异常：水合 JSON 解析失败时内部清理损坏缓存并降级到初始状态，不向组件外抛出。
 */
