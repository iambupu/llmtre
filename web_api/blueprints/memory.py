from __future__ import annotations

from typing import Any

from flask import Blueprint, request

from web_api.service import (
    MAX_MEMORY_TURNS,
    MIN_MEMORY_TURNS,
    build_memory,
    error,
    get_runtime_context,
    get_session,
    logger,
    now_iso,
    parse_json_body,
    success,
    validate_request_id,
    validate_session_id,
)

memory_blueprint = Blueprint("memory", __name__, url_prefix="/api/sessions/<session_id>/memory")


@memory_blueprint.get("")
def get_memory(session_id: str) -> tuple[Any, int]:
    """
    功能：查询会话记忆摘要或原始片段文本。
    入参：session_id（path），format（query，summary/raw）。
    出参：tuple[Any, int]，返回记忆文本与 recent_turns。
    异常：format 非法时返回 INVALID_ARGUMENT。
    """
    if not validate_session_id(session_id):
        logger.warning("get_memory 参数非法: session_id=%s", session_id)
        return error("INVALID_ARGUMENT", "session_id 格式非法", 400)
    session = get_session(session_id)
    if session is None:
        logger.warning("get_memory 会话不存在: session_id=%s", session_id)
        return error("SESSION_NOT_FOUND", "session_id 不存在", 404)
    fmt = str(request.args.get("format", "summary"))
    if fmt not in {"summary", "raw"}:
        logger.warning("get_memory 参数非法: format=%s", fmt)
        return error("INVALID_ARGUMENT", "format 仅支持 summary/raw", 400)

    context = get_runtime_context()
    max_turns = int(session["memory_policy"]["max_turns"])
    recent_turns = context.session_store.get_recent_story_turns_for_memory(
        session_id=session_id,
        max_turns=max_turns,
    )
    summary, recent_turns = build_memory(recent_turns, max_turns)
    text = summary if fmt == "summary" else "\n".join(item["text"] for item in recent_turns)
    logger.info("get_memory 查询成功: session_id=%s format=%s", session_id, fmt)
    return success(
        {
            "session_id": session_id,
            "format": fmt,
            "summary": text,
            "recent_turns": recent_turns,
            "token_estimate": max(0, len(text) // 2),
        }
    )


@memory_blueprint.post("/refresh")
def refresh_memory(session_id: str) -> tuple[Any, int]:
    """
    功能：手动重建记忆摘要并更新会话记忆策略窗口。
    入参：session_id（path）和 JSON（request_id、max_turns）。
    出参：tuple[Any, int]，返回 summary 与覆盖回合区间。
    异常：参数非法返回 INVALID_ARGUMENT；会话缺失返回 SESSION_NOT_FOUND。
    """
    if not validate_session_id(session_id):
        logger.warning("refresh_memory 参数非法: session_id=%s", session_id)
        return error("INVALID_ARGUMENT", "session_id 格式非法", 400)
    session = get_session(session_id)
    if session is None:
        logger.warning("refresh_memory 会话不存在: session_id=%s", session_id)
        return error("SESSION_NOT_FOUND", "session_id 不存在", 404)
    body = parse_json_body()
    request_id = validate_request_id(body)
    if request_id is None:
        logger.warning("refresh_memory 参数非法: request_id 缺失或格式非法")
        return error("INVALID_ARGUMENT", "request_id 缺失或格式非法", 400)
    max_turns = body.get("max_turns", session["memory_policy"]["max_turns"])
    if not isinstance(max_turns, int) or not (MIN_MEMORY_TURNS <= max_turns <= MAX_MEMORY_TURNS):
        logger.warning("refresh_memory 参数非法: max_turns=%s", max_turns)
        return error("INVALID_ARGUMENT", "max_turns 需在 5..100", 400)

    context = get_runtime_context()
    session_lock = context.get_session_lock(session_id)
    with session_lock:
        existing = context.session_store.get_idempotent_response(
            scope="refresh_memory",
            session_id=session_id,
            request_id=request_id,
        )
        if existing is not None:
            logger.info(
                "refresh_memory 幂等命中: session_id=%s request_id=%s",
                session_id,
                request_id,
            )
            return success(existing)
        memory_policy = {"mode": "auto", "max_turns": max_turns}
        context.session_store.update_memory_policy(
            session_id=session_id,
            memory_policy=memory_policy,
            now_iso=now_iso(),
        )
        turns = context.session_store.get_recent_story_turns_for_memory(
            session_id=session_id,
            max_turns=max_turns,
        )
        summary, _ = build_memory(turns, max_turns)
        context.session_store.update_memory_summary(
            session_id=session_id,
            memory_summary=summary,
            now_iso=now_iso(),
        )
        if turns:
            covered = {
                "from_session_turn_id": turns[max(0, len(turns) - max_turns)]["session_turn_id"],
                "to_session_turn_id": turns[-1]["session_turn_id"],
            }
        else:
            covered = {"from_session_turn_id": 0, "to_session_turn_id": 0}
        payload = {"session_id": session_id, "summary": summary, "covered_turn_range": covered}
        context.session_store.save_idempotent_response(
            scope="refresh_memory",
            session_id=session_id,
            request_id=request_id,
            response_payload=payload,
        )
        logger.info(
            "refresh_memory 重建成功: session_id=%s max_turns=%s",
            session_id,
            max_turns,
        )
        return success(payload)
