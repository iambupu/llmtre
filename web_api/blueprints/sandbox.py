"""
功能：提供叙事沙盒提交与回滚相关 Flask 路由。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from flask import Blueprint
from pydantic import ValidationError

from state.contracts.sandbox import SandboxDiffMode
from web_api.narrative_memory import build_narrative_memory_items
from web_api.sandbox_diff import (
    build_sandbox_diff_summary,
    build_unavailable_sandbox_diff_summary,
)
from web_api.service import (
    TurnExecutionError,
    build_memory,
    error,
    get_runtime_context,
    get_session,
    logger,
    new_trace_id,
    now_iso,
    parse_json_body,
    run_turn,
    success,
    sync_session_agent_memory_file,
    validate_request_id,
    validate_session_id,
)

sandbox_blueprint = Blueprint("sandbox", __name__, url_prefix="/api/sessions/<session_id>/sandbox")


@dataclass(slots=True)
class _SandboxActionInput:
    """
    功能：封装一次沙盒控制动作的路由参数。
    入参：session_id/request_id/action_text/flag_key/scope 均来自已校验的 HTTP 请求上下文。
    出参：无；作为内部参数对象传递。
    异常：不抛异常；字段合法性由路由入口校验。
    """

    session_id: str
    request_id: str
    action_text: str
    flag_key: str
    scope: str


@dataclass(slots=True)
class _SandboxResponseInput:
    """
    功能：封装构造沙盒动作响应所需的运行结果字段。
    入参：payload 为 run_turn 响应；trace 为可追加阶段的 trace 对象；
        sandbox_diff 为动作执行前捕获的 Active/Shadow 差异摘要。
    出参：无；作为内部参数对象传递。
    异常：不抛异常；响应构造函数负责校验 trace 类型。
    """

    session_id: str
    request_id: str
    scope: str
    flag_key: str
    payload: dict[str, Any]
    persisted_turn_id: int
    trace: dict[str, Any]
    sandbox_diff: dict[str, Any]


@dataclass(slots=True)
class _SandboxPersistInput:
    """
    功能：封装沙盒动作落盘、记忆更新和响应缓存所需的上下文。
    入参：context 为 API 运行时；fresh_session 为锁内最新会话；payload 为 run_turn 结果。
    出参：无；作为内部参数对象传递。
    异常：不抛异常；落盘函数负责抛出 SQL、JSON 或校验异常。
    """

    context: Any
    action: _SandboxActionInput
    fresh_session: dict[str, Any]
    payload: dict[str, Any]
    sandbox_diff: dict[str, Any]


def _build_sandbox_response_payload(response: _SandboxResponseInput) -> dict[str, Any]:
    """
    功能：构造沙盒动作响应并补齐最小 trace 阶段，供幂等缓存与最终响应复用。
    入参：response（_SandboxResponseInput）：已完成执行和落盘的沙盒动作响应上下文。
    出参：dict[str, Any]，包含沙盒动作最小响应字段与 trace。
    异常：trace 非对象时抛 ValueError，交由上层统一转 500。
    """
    trace = response.trace
    if not isinstance(trace, dict):
        raise ValueError("trace 结构非法")
    stages = trace.get("stages")
    if isinstance(stages, list):
        stages.append(
            {
                "stage": "api.persisted",
                "status": "ok",
                "at": now_iso(),
                "detail": {
                    "session_turn_id": response.persisted_turn_id,
                    "scope": response.scope,
                },
            }
        )
    return {
        "session_id": response.session_id,
        "session_turn_id": response.persisted_turn_id,
        "runtime_turn_id": response.payload["runtime_turn_id"],
        "trace_id": response.payload["trace_id"],
        "request_id": response.request_id,
        "trace": trace,
        response.flag_key: True,
        "sandbox_diff": response.sandbox_diff,
    }


def _build_sandbox_post_run_error(
    payload: dict[str, Any] | None,
    stage: str,
    err: Exception,
    scope: str,
) -> tuple[str, dict[str, Any]]:
    """
    功能：构造沙盒 post-run 异常的回传 trace，确保失败链路与 run_turn trace_id 连通。
    入参：payload（dict[str, Any] | None）：run_turn 输出；stage（str）：失败阶段；
        err（Exception）：原始异常；scope（str）：幂等作用域。
    出参：tuple[str, dict[str, Any]]，分别为 trace_id 与 trace。
    异常：函数内部不抛异常；结构异常时降级为最小 trace。
    """
    trace_id = str(payload.get("trace_id")) if isinstance(payload, dict) else new_trace_id()
    trace = payload.get("trace") if isinstance(payload, dict) else None
    if not isinstance(trace, dict):
        trace = {"trace_id": trace_id, "stages": [], "errors": []}
    trace["trace_id"] = trace_id
    stages = trace.get("stages")
    if not isinstance(stages, list):
        trace["stages"] = []
        stages = trace["stages"]
    stages.append(
        {
            "stage": stage,
            "status": "failed",
            "at": now_iso(),
            "detail": {"error": str(err), "scope": scope},
        }
    )
    errors = trace.get("errors")
    if not isinstance(errors, list):
        trace["errors"] = []
        errors = trace["errors"]
    errors.append({"stage": stage, "error": str(err)})
    return trace_id, trace


def _validate_sandbox_preconditions(
    context: Any,
    current_session: dict[str, Any],
) -> tuple[bool, tuple[Any, int] | None]:
    """
    功能：校验沙盒控制动作的前置条件，避免非沙盒会话或空 Shadow 触发破坏性合并。
    入参：context（Any）：API 运行时上下文；current_session（dict[str, Any]）：锁内最新会话快照。
    出参：tuple[bool, tuple[Any, int] | None]，首值表示是否通过校验；未通过时第二项为错误响应。
    异常：函数内部不抛异常；运行时上下文缺失时按标准 INTERNAL_ERROR 响应降级。
    """
    if not bool(current_session.get("sandbox_mode", False)):
        return False, error(
            "SANDBOX_STATE_INVALID",
            "当前会话不在沙盒模式，无法执行沙盒控制动作",
            409,
        )
    if context.main_loop is None:
        return False, error("INTERNAL_ERROR", "主循环未初始化", 500)
    if not context.main_loop.db_updater.is_sandbox_owner(current_session["session_id"]):
        return False, error(
            "SANDBOX_OWNER_MISMATCH",
            "当前会话未持有沙盒租约，无法执行并入或回滚",
            409,
        )
    # 提交/丢弃都依赖现存 Shadow 快照；若不存在则拒绝执行，防止 merge 空集清空 Active。
    if not context.main_loop.db_updater.has_shadow_state():
        return False, error(
            "SHADOW_STATE_NOT_FOUND",
            "未检测到可用的沙盒快照，无法执行并入或回滚",
            409,
        )
    return True, None


def _build_sandbox_diff_for_context(
    context: Any,
    session_id: str,
    trace_id: str,
    mode: SandboxDiffMode,
) -> dict[str, Any]:
    """
    功能：从当前运行时上下文生成沙盒差异摘要，缺少 DBUpdater 时显式降级。
    入参：context（Any）：API 运行时；session_id（str）：会话 ID；
        trace_id（str）：追踪 ID；mode（SandboxDiffMode）：preview/committed/discarded。
    出参：dict[str, Any]，符合 SandboxDiffSummary JSON 契约。
    异常：不抛异常；不可比较时返回 diagnostics。
    """
    db_updater = getattr(getattr(context, "main_loop", None), "db_updater", None)
    db_path = str(getattr(db_updater, "db_path", "") or "")
    if not db_path:
        return build_unavailable_sandbox_diff_summary(
            session_id=session_id,
            trace_id=trace_id,
            mode=mode,
            reason="无法生成沙盒差异：运行时未暴露 db_path",
        )
    return build_sandbox_diff_summary(
        db_path=db_path,
        session_id=session_id,
        trace_id=trace_id,
        mode=mode,
    )


def _build_sandbox_memory_summary(
    context: Any,
    session_id: str,
    fresh_session: dict[str, Any],
    action_text: str,
    payload: dict[str, Any],
) -> str:
    """
    功能：基于沙盒动作结果生成落盘前的会话记忆摘要。
    入参：context（Any）：API 运行时上下文；session_id（str）：会话 ID；
        fresh_session（dict[str, Any]）：锁内最新会话；action_text（str）：沙盒动作文本；
        payload（dict[str, Any]）：run_turn 输出。
    出参：str，新一轮 memory_summary。
    异常：近期回合读取或摘要构造异常向上抛出，由 post-run 错误处理写入 trace。
    """
    recent_turns = context.session_store.get_recent_turns_for_memory(
        session_id=session_id,
        max_turns=int(fresh_session["memory_policy"]["max_turns"]),
    )
    draft_turn_id = int(fresh_session["current_turn_id"]) + 1
    draft_turns = recent_turns + [
        {
            "turn_id": draft_turn_id,
            "session_turn_id": draft_turn_id,
            "user_input": action_text,
            "final_response": payload["final_response"],
        }
    ]
    memory_summary, _ = build_memory(
        turns=draft_turns,
        max_turns=int(fresh_session["memory_policy"]["max_turns"]),
    )
    return memory_summary


def _persist_sandbox_action_result(persistence: _SandboxPersistInput) -> dict[str, Any]:
    """
    功能：持久化沙盒控制动作结果、记忆项和幂等响应。
    入参：persistence（_SandboxPersistInput）：锁内会话、动作参数、run_turn 结果与沙盒差异。
    出参：dict[str, Any]，已可直接 success(...) 的响应负载。
    异常：持久化、响应构造或记忆写入异常向上抛出，由调用方转换为 INTERNAL_ERROR。
    """
    context = persistence.context
    action = persistence.action
    payload = persistence.payload
    fresh_session = persistence.fresh_session
    memory_summary = _build_sandbox_memory_summary(
        context=context,
        session_id=action.session_id,
        fresh_session=fresh_session,
        action_text=action.action_text,
        payload=payload,
    )
    raw_trace = payload.get("trace")
    trace_payload: dict[str, Any] = raw_trace if isinstance(raw_trace, dict) else {}
    response_payload, _ = context.session_store.persist_turn_result_with_idempotency(
        scope=action.scope,
        session_id=action.session_id,
        request_id=action.request_id,
        user_input=action.action_text,
        turn_result=payload,
        memory_summary=memory_summary,
        now_iso=now_iso(),
        response_builder=lambda persisted_turn_id: _build_sandbox_response_payload(
            _SandboxResponseInput(
                session_id=action.session_id,
                request_id=action.request_id,
                scope=action.scope,
                flag_key=action.flag_key,
                payload=payload,
                persisted_turn_id=persisted_turn_id,
                trace=trace_payload,
                sandbox_diff=persistence.sandbox_diff,
            )
        ),
        memory_items_builder=lambda persisted_turn_id: build_narrative_memory_items(
            session_id=action.session_id,
            session_turn_id=persisted_turn_id,
            user_input=action.action_text,
            turn_result=payload,
        ),
    )
    sync_session_agent_memory_file(context, action.session_id, memory_summary)
    return cast(dict[str, Any], response_payload)


def _load_sandbox_action_state(
    context: Any,
    action: _SandboxActionInput,
) -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    """
    功能：在沙盒锁内读取幂等缓存、会话快照并校验控制动作前置条件。
    入参：context（Any）：API 运行时上下文；action（_SandboxActionInput）：沙盒动作参数。
    出参：tuple[dict[str, Any] | None, tuple[Any, int] | None]；
        第一项为通过校验的会话，第二项为可直接返回的缓存或错误响应。
    异常：不捕获底层存储异常；锁内读取失败应由 Flask 错误链路暴露为服务异常。
    """
    existing = context.session_store.get_idempotent_response(
        scope=action.scope,
        session_id=action.session_id,
        request_id=action.request_id,
    )
    if existing is not None:
        return None, success(existing)
    fresh_session = get_session(action.session_id)
    if fresh_session is None:
        return None, error("SESSION_NOT_FOUND", "session_id 不存在", 404)
    ok, failure_response = _validate_sandbox_preconditions(context, fresh_session)
    if not ok and failure_response is not None:
        return None, failure_response
    return fresh_session, None


def _execute_sandbox_action_turn(
    context: Any,
    action: _SandboxActionInput,
    fresh_session: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, tuple[Any, int] | None]:
    """
    功能：执行沙盒控制动作对应的主循环回合，并在执行前捕获沙盒差异。
    入参：context（Any）：API 运行时上下文；action（_SandboxActionInput）：动作参数；
        fresh_session（dict[str, Any]）：锁内最新会话。
    出参：tuple[payload | None, sandbox_diff | None, failure_response | None]。
    异常：TurnExecutionError 与未知异常在函数内转换为标准错误响应并记录日志。
    """
    try:
        trace_id = new_trace_id()
        diff_mode: SandboxDiffMode = "committed" if action.flag_key == "committed" else "discarded"
        sandbox_diff = _build_sandbox_diff_for_context(
            context=context,
            session_id=action.session_id,
            trace_id=trace_id,
            mode=diff_mode,
        )
        runtime_character_id = str(
            fresh_session.get("runtime_character_id") or fresh_session["character_id"]
        )
        payload = run_turn(
            session=fresh_session,
            user_input=action.action_text,
            character_id=runtime_character_id,
            sandbox_mode=True,
            trace_id=trace_id,
            request_id=action.request_id,
        )
        return payload, sandbox_diff, None
    except TurnExecutionError as err:
        return (
            None,
            None,
            error(
                err.error_code,
                f"沙盒动作执行失败: {err}",
                err.status_code,
                trace_id=err.trace_id,
                trace=err.trace,
            ),
        )
    except Exception as err:  # noqa: BLE001
        logger.exception(
            "沙盒动作执行失败: scope=%s session_id=%s request_id=%s",
            action.scope,
            action.session_id,
            action.request_id,
        )
        return None, None, error("INTERNAL_ERROR", f"沙盒动作执行失败: {err}", 500)


def _persist_sandbox_action_or_error(
    context: Any,
    action: _SandboxActionInput,
    fresh_session: dict[str, Any],
    payload: dict[str, Any],
    sandbox_diff: dict[str, Any],
) -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    """
    功能：持久化沙盒动作结果；失败时补全 post-run trace 并转换为标准错误响应。
    入参：context（Any）：API 运行时上下文；action（_SandboxActionInput）：动作参数；
        fresh_session（dict[str, Any]）：锁内会话；payload（dict[str, Any]）：主循环输出；
        sandbox_diff（dict[str, Any]）：执行前捕获的差异摘要。
    出参：tuple[response_payload | None, failure_response | None]。
    异常：内部捕获落盘、校验和记忆同步异常，统一返回 INTERNAL_ERROR。
    """
    try:
        response_payload = _persist_sandbox_action_result(
            _SandboxPersistInput(
                context=context,
                action=action,
                fresh_session=fresh_session,
                payload=payload,
                sandbox_diff=sandbox_diff,
            )
        )
        return response_payload, None
    except Exception as err:  # noqa: BLE001
        stage = "api.response_built" if isinstance(err, ValidationError) else "api.persisted"
        trace_id, trace = _build_sandbox_post_run_error(payload, stage, err, action.scope)
        logger.exception(
            "沙盒动作 post-run 失败: stage=%s scope=%s session_id=%s request_id=%s",
            stage,
            action.scope,
            action.session_id,
            action.request_id,
        )
        return None, error(
            "INTERNAL_ERROR",
            f"沙盒动作执行失败: {err}",
            500,
            trace_id=trace_id,
            trace=trace,
        )


def _sandbox_action(action: _SandboxActionInput) -> tuple[Any, int]:
    """
    功能：执行沙盒控制动作（并入或丢弃）的共享逻辑。
    入参：action（_SandboxActionInput）：沙盒动作路由参数。
    出参：tuple[Any, int]，统一响应结构。
    异常：主循环异常捕获后转换为 INTERNAL_ERROR，避免异常泄漏到接口层。
    """

    context = get_runtime_context()
    session_lock = context.get_session_lock(action.session_id)
    with session_lock:
        fresh_session, failure_response = _load_sandbox_action_state(context, action)
        if failure_response is not None:
            return failure_response
        session_payload = cast(dict[str, Any], fresh_session)
        payload, sandbox_diff, failure_response = _execute_sandbox_action_turn(
            context,
            action,
            session_payload,
        )
        if failure_response is not None:
            return failure_response
        response_payload, failure_response = _persist_sandbox_action_or_error(
            context=context,
            action=action,
            fresh_session=session_payload,
            payload=cast(dict[str, Any], payload),
            sandbox_diff=cast(dict[str, Any], sandbox_diff),
        )
        if failure_response is not None:
            return failure_response
        return success(cast(dict[str, Any], response_payload))


@sandbox_blueprint.post("/commit")
def commit_sandbox(session_id: str) -> tuple[Any, int]:
    """
    功能：触发沙盒并入主线。
    入参：session_id（path）和 JSON（request_id）。
    出参：tuple[Any, int]，成功返回 committed=true。
    异常：参数非法或会话不存在时返回标准错误码。
    """
    if not validate_session_id(session_id):
        return error("INVALID_ARGUMENT", "session_id 格式非法", 400)
    session = get_session(session_id)
    if session is None:
        return error("SESSION_NOT_FOUND", "session_id 不存在", 404)
    body = parse_json_body()
    request_id = validate_request_id(body)
    if request_id is None:
        return error("INVALID_ARGUMENT", "request_id 缺失或格式非法", 400)
    return _sandbox_action(
        _SandboxActionInput(
            session_id=session_id,
            request_id=request_id,
            action_text="并入主线",
            flag_key="committed",
            scope="sandbox_commit",
        )
    )


@sandbox_blueprint.post("/discard")
def discard_sandbox(session_id: str) -> tuple[Any, int]:
    """
    功能：触发沙盒回滚丢弃。
    入参：session_id（path）和 JSON（request_id）。
    出参：tuple[Any, int]，成功返回 discarded=true。
    异常：参数非法或会话不存在时返回标准错误码。
    """
    if not validate_session_id(session_id):
        return error("INVALID_ARGUMENT", "session_id 格式非法", 400)
    session = get_session(session_id)
    if session is None:
        return error("SESSION_NOT_FOUND", "session_id 不存在", 404)
    body = parse_json_body()
    request_id = validate_request_id(body)
    if request_id is None:
        return error("INVALID_ARGUMENT", "request_id 缺失或格式非法", 400)
    return _sandbox_action(
        _SandboxActionInput(
            session_id=session_id,
            request_id=request_id,
            action_text="回滚沙盒",
            flag_key="discarded",
            scope="sandbox_discard",
        )
    )


@sandbox_blueprint.get("/diff")
def preview_sandbox_diff(session_id: str) -> tuple[Any, int]:
    """
    功能：预览当前沙盒 Active/Shadow 差异，不推进回合、不写记忆。
    入参：session_id（path）。
    出参：tuple[Any, int]，成功返回 sandbox_diff。
    异常：参数非法、会话不存在或前置条件不满足时返回标准错误码。
    """
    if not validate_session_id(session_id):
        return error("INVALID_ARGUMENT", "session_id 格式非法", 400)
    context = get_runtime_context()
    session_lock = context.get_session_lock(session_id)
    with session_lock:
        fresh_session = get_session(session_id)
        if fresh_session is None:
            return error("SESSION_NOT_FOUND", "session_id 不存在", 404)
        ok, failure_response = _validate_sandbox_preconditions(context, fresh_session)
        if not ok and failure_response is not None:
            return failure_response
        trace_id = new_trace_id()
        sandbox_diff = _build_sandbox_diff_for_context(
            context=context,
            session_id=session_id,
            trace_id=trace_id,
            mode="preview",
        )
        return success(
            {
                "session_id": session_id,
                "trace_id": trace_id,
                "sandbox_diff": sandbox_diff,
            }
        )
