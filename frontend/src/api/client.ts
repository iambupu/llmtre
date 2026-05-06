import { useDebugStore } from "@/stores/debugStore";
import type { ApiFailure, ApiSuccess } from "@/types";

export class ApiError extends Error {
  status: number;
  traceId?: string;
  code?: string;
  trace?: unknown;

  /**
   * 功能：封装 API 失败信息，保留 trace_id 与错误码用于调试。
   * 入参：message（string）错误信息；status（number）HTTP 状态码；traceId/code/trace 为可选调试上下文。
   * 出参：ApiError 实例。
   * 异常：无，构造函数本身不抛异常。
   */
  constructor(
    message: string,
    status: number,
    traceId?: string,
    code?: string,
    trace?: unknown
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.traceId = traceId;
    this.code = code;
    this.trace = trace;
  }
}

let requestCounter = 0;

/**
 * 功能：生成符合后端约束的 request_id，避免前端重复提交时缺失幂等键。
 * 入参：scope（string）请求作用域前缀。
 * 出参：string，形如 `req{scope}_{timestamp}_{counter}`。
 * 异常：无。
 */
export function createRequestId(scope: string): string {
  requestCounter += 1;
  return `req${scope}_${Date.now().toString(36)}_${requestCounter.toString(36)}`;
}

/**
 * 功能：统一发送 JSON 请求并解析成功/失败响应，且把请求/trace 写入调试仓库。
 * 入参：path（string）同源 API 路径；init（RequestInit）请求配置。
 * 出参：Promise<ApiSuccess<T>>。
 * 异常：当网络失败、非 JSON 响应或业务 `ok=false` 时抛出 ApiError。
 */
export async function requestJson<T>(
  path: string,
  init?: RequestInit
): Promise<ApiSuccess<T>> {
  const method = init?.method ?? "GET";
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  const text = await response.text();
  let data: ApiSuccess<T> | ApiFailure | null = null;
  try {
    data = text ? (JSON.parse(text) as ApiSuccess<T> | ApiFailure) : null;
  } catch {
    throw new ApiError("响应不是合法 JSON", response.status);
  }

  useDebugStore.getState().setLastRequest({
    method,
    path,
    status: response.status,
    body: init?.body ?? null,
    response: data,
  });

  const traceId = data && "trace_id" in data ? data.trace_id : undefined;
  if (traceId) {
    useDebugStore.getState().setTraceId(traceId);
  }

  if (!response.ok || !data || ("ok" in data && data.ok === false)) {
    const failure = (data ?? {}) as ApiFailure;
    throw new ApiError(
      failure.error?.message ?? `请求失败: ${response.status}`,
      response.status,
      failure.trace_id ?? traceId,
      failure.error?.code,
      failure.trace
    );
  }

  return data as ApiSuccess<T>;
}
