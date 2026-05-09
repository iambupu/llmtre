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
    入参：HTTP JSON，请求体包含 request_id、character_id、sandbox_mode，可选 pack_id/scenario_id。
    出参：tuple[Any, int]，成功返回 201 与会话元数据。
    异常：参数非法时返回 INVALID_ARGUMENT；pack 不存在返回 PACK_NOT_FOUND；
        内部异常由 Flask 统一处理为 500。
    """
    body = parse_json_body()
    request_id = validate_request_id(body)
    if request_id is None:
        # 关键分支：无 request_id 时直接拒绝，日志用于定位前端幂等键丢失问题。
        logger.warning("create_session 参数非法: request_id 缺失或格式非法")
        return error("INVALID_ARGUMENT", "request_id 缺失或格式非法", 400)

    context = get_runtime_context()
    # 幂等边界：同一 request_id 的历史创建结果优先于当前请求体语义校验，
    # 避免重复提交时后续 pack/persona 变化覆盖或阻断已冻结的会话绑定。
    cached_payload = context.session_store.get_idempotent_response(
        "create_session",
        "",
        request_id,
    )
    if cached_payload is not None:
        logger.info("create_session 幂等预命中: request_id=%s", request_id)
        return success(cached_payload, status_code=201)

    character_id = str(body.get("character_id", "player_01"))
    if not validate_character_id(character_id):
        logger.warning("create_session 参数非法: character_id 格式非法=%s", character_id)
        return error("INVALID_ARGUMENT", "character_id 格式非法", 400)
    if not ensure_character_available(character_id):
        logger.warning("create_session 角色不存在: character_id=%s", character_id)
        return error("CHARACTER_NOT_FOUND", "角色不存在，无法创建会话", 404)

    sandbox_mode = bool(body.get("sandbox_mode", False))
    pack_metadata: dict[str, Any] = {}
    raw_pack_id = body.get("pack_id")
    raw_scenario_id = body.get("scenario_id") or "default"
    if raw_pack_id is not None:
        pack_id = str(raw_pack_id).strip()
        scenario_id = str(raw_scenario_id).strip() or "default"
        if not validate_character_id(pack_id) or not validate_character_id(scenario_id):
            logger.warning(
                "create_session 参数非法: pack_id=%s scenario_id=%s",
                pack_id,
                scenario_id,
            )
            return error("INVALID_ARGUMENT", "pack_id 或 scenario_id 格式非法", 400)
        # A2-Core 会话绑定只接受 registry 已校验通过的 pack，避免坏包进入持久化会话。
        context.story_pack_registry.refresh()
        bundle = context.story_pack_registry.get(pack_id)
        if bundle is None:
            logger.warning("create_session 剧本包不存在或未通过校验: pack_id=%s", pack_id)
            return error("PACK_NOT_FOUND", "pack_id 不存在或未通过校验", 404)
        if scenario_id != bundle.summary.scenario_id:
            logger.warning(
                "create_session scenario_id 不匹配: pack_id=%s scenario_id=%s expected=%s",
                pack_id,
                scenario_id,
                bundle.summary.scenario_id,
            )
            return error("PACK_NOT_FOUND", "scenario_id 不存在或未通过校验", 404)
        pack_metadata = {
            "pack_id": bundle.summary.pack_id,
            "scenario_id": bundle.summary.scenario_id,
            "pack_version": bundle.summary.version,
            "compiled_artifact_hash": bundle.summary.compiled_artifact_hash,
        }
    persona_profile_raw = body.get("persona_profile", {})
    if persona_profile_raw is None:
        persona_profile_raw = {}
    if not isinstance(persona_profile_raw, dict):
        logger.warning("create_session 参数非法: persona_profile 不是对象")
        return error("INVALID_ARGUMENT", "persona_profile 必须是对象", 400)
    persona_profile = dict(persona_profile_raw)
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
    response_payload.update(pack_metadata)
    if persona_profile:
        response_payload["persona_profile"] = persona_profile
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
        pack_metadata=pack_metadata,
        persona_profile=persona_profile,
    )
    if not created:
        # 幂等边界：并发重放场景下事务内命中缓存，不重复创建会话。
        logger.info("create_session 幂等命中: request_id=%s", request_id)
        return success(persisted_payload, status_code=201)
    logger.info(
        "create_session 创建成功: session_id=%s character_id=%s pack_id=%s",
        session_id,
        character_id,
        pack_metadata.get("pack_id", ""),
    )
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
        "pack_id": session.get("pack_id"),
        "scenario_id": session.get("scenario_id"),
        "pack_version": session.get("pack_version"),
        "compiled_artifact_hash": session.get("compiled_artifact_hash"),
        "persona_profile": session.get("persona_profile", {}),
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
