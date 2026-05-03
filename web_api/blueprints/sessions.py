from __future__ import annotations

from typing import Any

from flask import Blueprint

from web_api.service import (
    DEFAULT_MEMORY_TURNS,
    build_initial_turn_payload,
    ensure_character_available,
    error,
    get_play_state,
    get_runtime_context,
    get_session,
    logger,
    new_session_id,
    now_iso,
    parse_json_body,
    success,
    validate_character_id,
    validate_request_id,
    validate_session_id,
)

sessions_blueprint = Blueprint("sessions", __name__, url_prefix="/api/sessions")


@sessions_blueprint.post("")
def create_session() -> tuple[Any, int]:
    """
    功能：创建会话并建立会话级并发控制与幂等索引。
    入参：HTTP JSON，请求体包含 request_id、character_id、sandbox_mode。
    出参：tuple[Any, int]，成功返回 201 与会话元数据。
    异常：参数非法时返回 INVALID_ARGUMENT；内部异常由 Flask 统一处理为 500。
    """
    body = parse_json_body()
    request_id = validate_request_id(body)
    if request_id is None:
        # 关键分支：无 request_id 时直接拒绝，日志用于定位前端幂等键丢失问题。
        logger.warning("create_session 参数非法: request_id 缺失或格式非法")
        return error("INVALID_ARGUMENT", "request_id 缺失或格式非法", 400)

    context = get_runtime_context()
    character_id = str(body.get("character_id", "player_01"))
    if not validate_character_id(character_id):
        logger.warning("create_session 参数非法: character_id 格式非法=%s", character_id)
        return error("INVALID_ARGUMENT", "character_id 格式非法", 400)
    if not ensure_character_available(character_id):
        logger.warning("create_session 角色不存在: character_id=%s", character_id)
        return error("CHARACTER_NOT_FOUND", "角色不存在，无法创建会话", 404)

    sandbox_mode = bool(body.get("sandbox_mode", False))
    created_at = now_iso()
    session_id = new_session_id()
    memory_policy = {"mode": "auto", "max_turns": DEFAULT_MEMORY_TURNS}
    response_payload = {
        "session_id": session_id,
        "character_id": character_id,
        "sandbox_mode": sandbox_mode,
        "current_session_turn_id": 0,
        "created_at": created_at,
    }
    # 新会话首屏必须先有 GM 开场叙事，再把同一叙事生成的选项返回前端。
    response_payload.update(build_initial_turn_payload(character_id, sandbox_mode))
    persisted_payload, created = context.session_store.create_session_with_idempotency(
        scope="create_session",
        request_id=request_id,
        session_id=session_id,
        character_id=character_id,
        sandbox_mode=sandbox_mode,
        now_iso=created_at,
        memory_policy=memory_policy,
        response_payload=response_payload,
    )
    if not created:
        # 幂等边界：并发重放场景下事务内命中缓存，不重复创建会话。
        logger.info("create_session 幂等命中: request_id=%s", request_id)
        return success(persisted_payload, status_code=201)
    logger.info("create_session 创建成功: session_id=%s character_id=%s", session_id, character_id)
    return success(persisted_payload, status_code=201)


@sessions_blueprint.get("/<session_id>")
def get_session_detail(session_id: str) -> tuple[Any, int]:
    """
    功能：查询会话元数据与回合进度。
    入参：session_id（path）。
    出参：tuple[Any, int]，存在返回 200，不存在返回 404。
    异常：参数非法返回 INVALID_ARGUMENT。
    """
    if not validate_session_id(session_id):
        logger.warning("get_session_detail 参数非法: session_id=%s", session_id)
        return error("INVALID_ARGUMENT", "session_id 格式非法", 400)
    session = get_session(session_id)
    if session is None:
        logger.warning("get_session_detail 会话不存在: session_id=%s", session_id)
        return error("SESSION_NOT_FOUND", "session_id 不存在", 404)
    payload = {
        "session_id": session["session_id"],
        "character_id": session["character_id"],
        "sandbox_mode": session["sandbox_mode"],
        "current_session_turn_id": session["current_turn_id"],
        "last_active_at": session["last_active_at"],
    }
    payload.update(
        get_play_state(
            str(session["character_id"]),
            bool(session["sandbox_mode"]),
            recent_memory=str(session.get("memory_summary", "")),
        )
    )
    logger.info("get_session_detail 查询成功: session_id=%s", session_id)
    return success(payload)
