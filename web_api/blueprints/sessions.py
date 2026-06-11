"""
功能：提供 Web 会话创建、查询和历史读取相关 Flask 路由。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import Blueprint, request

from agents.agent_context import delete_session_agent_memory, initialize_session_agent_memory
from game_workflows.main_loop_scene_helpers import derive_character_status
from game_workflows.quest_state_helpers import dump_quest_states, normalize_quest_states
from state.contracts.story_pack import StoryPackBundle
from tools.packs.generation_service import StoryPackGenerationService
from tools.packs.trigger_effects import apply_trigger_effects
from tools.packs.trigger_evaluator import TriggerEvaluator
from web_api.service import (
    DEFAULT_MEMORY_TURNS,
    _pack_active_quests_json,
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
from web_api.session_store import build_session_runtime_character_id

sessions_blueprint = Blueprint("sessions", __name__, url_prefix="/api/sessions")


def _parse_session_list_limit() -> tuple[int | None, tuple[Any, int] | None]:
    """
    功能：解析会话列表 limit 查询参数并限制最大返回数量。
    入参：无（读取当前 Flask request.args）。
    出参：tuple[int | None, tuple[Any, int] | None]，成功返回 limit，失败返回错误响应。
    异常：不抛异常；非整数或越界统一返回 INVALID_ARGUMENT。
    """
    raw_limit = request.args.get("limit", "20")
    try:
        limit = int(raw_limit)
    except ValueError:
        logger.warning("list_sessions 参数非法: limit=%s", raw_limit)
        return None, error("INVALID_ARGUMENT", "limit 必须为整数", 400)
    if limit < 1 or limit > 100:
        logger.warning("list_sessions 参数越界: limit=%s", limit)
        return None, error("INVALID_ARGUMENT", "limit 超出范围，允许 1-100", 400)
    return limit, None


def _enrich_session_summary(context: Any, summary: dict[str, Any]) -> dict[str, Any]:
    """
    功能：用 Story Pack registry 为会话摘要补齐玩家可读的剧本标题和场景标题。
    入参：context（Any）：API 运行时上下文；summary（dict[str, Any]）：持久层会话摘要。
    出参：dict[str, Any]，保留原始事实字段并追加 pack_title/current_scene_title。
    异常：不抛异常；registry 缺包或场景缺失时按空标题降级。
    """
    enriched = dict(summary)
    pack_id = str(enriched.get("pack_id") or "").strip()
    if not pack_id:
        enriched.setdefault("pack_title", None)
        enriched.setdefault("current_scene_title", None)
        return enriched
    bundle = context.story_pack_registry.get(pack_id)
    if bundle is None:
        enriched.setdefault("pack_title", None)
        enriched.setdefault("current_scene_title", None)
        return enriched

    enriched["pack_title"] = bundle.summary.title
    current_scene_id = str(enriched.get("current_scene_id") or "").strip()
    if not current_scene_id:
        current_scene_id = bundle.summary.start_scene_id
        enriched["current_scene_id"] = current_scene_id
    scene = bundle.scenes.get(current_scene_id)
    enriched["current_scene_title"] = scene.display_name if scene is not None else None
    return enriched


@dataclass(frozen=True)
class _CreateSessionDraft:
    """
    功能：承载 create_session 进入持久化事务前已经冻结的会话草稿。
    入参：由 dataclass 字段提供，包含会话 ID、角色 ID、运行角色 ID、时间、记忆策略和响应体。
    出参：无显式返回值；实例作为不可变数据包传递。
    异常：dataclass 构造不会主动校验字段语义，调用方必须先完成请求校验。
    """

    session_id: str
    character_id: str
    runtime_character_id: str
    sandbox_mode: bool
    created_at: str
    memory_policy: dict[str, Any]
    initial_location_id: str | None
    initial_session_metadata: dict[str, Any]
    initial_state_flags: list[str]
    initial_granted_items: list[dict[str, Any]]
    response_payload: dict[str, Any]


def _unique_texts(values: Any) -> list[str]:
    """
    功能：把任意序列规整为去重后的非空字符串列表。
    入参：values（Any）：通常来自 physics_diff 的状态标签或 fired_trigger_ids。
    出参：list[str]，保留首次出现顺序。
    异常：不抛异常；非列表输入按空列表降级。
    """
    result: list[str] = []
    seen: set[str] = set()
    if not isinstance(values, list):
        return result
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _build_initial_pack_runtime(
    bundle: StoryPackBundle | None,
    runtime_character_id: str,
) -> dict[str, Any]:
    """
    功能：把“创建会话”解释为进入剧本起点场景，计算初始 trigger/effect/quest 元数据。
    入参：bundle（StoryPackBundle | None）：已校验剧本包；
        runtime_character_id（str）：会话运行角色。
    出参：dict[str, Any]，包含 session_metadata、state_flags、granted_items、
        trigger_events 与 quest_states。
    异常：触发器 effect 内部按各自策略降级；本函数不直接抛业务异常。
    """
    if bundle is None:
        return {
            "session_metadata": {},
            "state_flags": [],
            "granted_items": [],
            "trigger_events": [],
            "quest_states": [],
        }
    quest_states = normalize_quest_states([], bundle.quests)
    evaluator = TriggerEvaluator(list(bundle.triggers.values()), set())
    trigger_events = evaluator.evaluate(
        "enter_scene",
        {"scene_id": bundle.summary.start_scene_id},
    )
    trigger_event_dicts = [event.model_dump(mode="json") for event in trigger_events]
    physics_diff: dict[str, Any] = {}
    apply_trigger_effects(
        trigger_events=trigger_event_dicts,
        pack_triggers=bundle.triggers,
        physics_diff=physics_diff,
        quest_states=quest_states,
        active_character_id=runtime_character_id,
    )
    quest_state_dicts = dump_quest_states(quest_states)
    fired_trigger_ids = sorted(evaluator.get_fired_ids())
    return {
        "session_metadata": {
            "fired_trigger_ids": fired_trigger_ids,
            "quest_states": quest_state_dicts,
        },
        "state_flags": _unique_texts(physics_diff.get("state_flags_add")),
        "granted_items": [
            item for item in physics_diff.get("granted_items", []) if isinstance(item, dict)
        ],
        "trigger_events": trigger_event_dicts,
        "quest_states": quest_state_dicts,
        "fired_trigger_ids": fired_trigger_ids,
    }


def _merge_initial_runtime_into_payload(
    context: Any,
    payload: dict[str, Any],
    *,
    pack_id: str | None,
    runtime_character_id: str,
    initial_runtime: dict[str, Any],
) -> None:
    """
    功能：把起点触发器产生的角色状态、任务状态和触发摘要合并进创建会话响应。
    入参：context（Any）：运行时上下文；payload（dict[str, Any]）：待返回响应；
        pack_id（str | None）：当前剧本包 ID；runtime_character_id（str）：运行角色；
        initial_runtime（dict[str, Any]）：_build_initial_pack_runtime 的结果。
    出参：None，原地更新 payload。
    异常：不抛异常；缺失 active_character 或 scene_snapshot 时跳过对应展示补丁。
    """
    state_flags = _unique_texts(initial_runtime.get("state_flags"))
    active_character = payload.get("active_character")
    if isinstance(active_character, dict):
        active_character["id"] = runtime_character_id
        active_character["state_flags"] = state_flags
        rules = getattr(context.main_loop, "rules", {}) if context.main_loop is not None else {}
        status_summary, status_effects, status_context = derive_character_status(
            active_character,
            state_flags,
            rules=rules,
        )
        active_character["status_summary"] = status_summary
        active_character["status_effects"] = status_effects
        active_character["status_context"] = status_context

    quest_states = initial_runtime.get("quest_states", [])
    scene_snapshot = payload.get("scene_snapshot")
    if isinstance(scene_snapshot, dict):
        scene_snapshot["active_quests"] = _pack_active_quests_json(
            context,
            pack_id,
            quest_states,
        )
    payload["trigger_events"] = initial_runtime.get("trigger_events", [])
    payload["fired_trigger_ids"] = initial_runtime.get("fired_trigger_ids", [])
    payload["quest_states"] = quest_states


def _validate_create_session_request_id(
    body: dict[str, Any],
) -> tuple[str | None, tuple[Any, int] | None]:
    """
    功能：校验 create_session 幂等请求 ID。
    入参：body（dict[str, Any]）：已解析的 JSON 请求体。
    出参：tuple[str | None, tuple[Any, int] | None]，成功返回 request_id，失败返回错误响应。
    异常：不抛异常；非法 request_id 统一返回 INVALID_ARGUMENT。
    """
    request_id = validate_request_id(body)
    if request_id is None:
        # 关键分支：无 request_id 时直接拒绝，日志用于定位前端幂等键丢失问题。
        logger.warning("create_session 参数非法: request_id 缺失或格式非法")
        return None, error("INVALID_ARGUMENT", "request_id 缺失或格式非法", 400)
    return request_id, None


def _get_cached_create_session_response(
    context: Any,
    request_id: str,
) -> dict[str, Any] | None:
    """
    功能：在请求语义校验前查询 create_session 幂等缓存。
    入参：context（Any）：运行时上下文；request_id（str）：幂等请求 ID。
    出参：dict[str, Any] | None，命中返回历史响应，未命中返回 None。
    异常：底层存储异常向上抛出，由 Flask 统一处理为 500。
    """
    cached_payload: dict[str, Any] | None = context.session_store.get_idempotent_response(
        "create_session",
        "",
        request_id,
    )
    return cached_payload


def _validate_create_session_character(
    body: dict[str, Any],
) -> tuple[str | None, tuple[Any, int] | None]:
    """
    功能：校验 create_session 基准角色 ID 与角色存在性。
    入参：body（dict[str, Any]）：已解析的 JSON 请求体。
    出参：tuple[str | None, tuple[Any, int] | None]，成功返回 character_id，失败返回错误响应。
    异常：不抛异常；格式非法和角色不存在分别返回 INVALID_ARGUMENT / CHARACTER_NOT_FOUND。
    """
    character_id = str(body.get("character_id", "player_01"))
    if not validate_character_id(character_id):
        logger.warning("create_session 参数非法: character_id 格式非法=%s", character_id)
        return None, error("INVALID_ARGUMENT", "character_id 格式非法", 400)
    if not ensure_character_available(character_id):
        logger.warning("create_session 角色不存在: character_id=%s", character_id)
        return None, error("CHARACTER_NOT_FOUND", "角色不存在，无法创建会话", 404)
    return character_id, None


def _resolve_create_session_pack_metadata(
    context: Any,
    body: dict[str, Any],
) -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    """
    功能：解析并校验 create_session 的 Story Pack 来源，必要时从 background 生成 pack。
    入参：context（Any）：运行时上下文；body（dict[str, Any]）：已解析请求体。
    出参：tuple[dict[str, Any] | None, tuple[Any, int] | None]，成功返回 pack_metadata，
        失败返回错误响应。
    异常：生成器或 registry 异常向上抛出；已知输入错误转换为稳定 API 错误。
    """
    raw_pack_id = body.get("pack_id")
    raw_scenario_id = body.get("scenario_id") or "default"
    # 自定义背景模式：没有显式 pack_id 时，必须先把 background 生成合法 Story Pack 再创建会话。
    if raw_pack_id is None:
        background: str = (body.get("background") or "").strip()
        if background:
            if len(background) > 2000:
                logger.warning("create_session background 超长: len=%s", len(background))
                return None, error("INVALID_ARGUMENT", "background 内容过长，限制 2000 字符", 400)
            gen_result = StoryPackGenerationService(
                context.story_pack_registry
            ).generate_from_background(background)
            if "error" in gen_result:
                logger.warning("create_session 剧本生成失败: %s", gen_result["error"])
                return None, error("SERVER_ERROR", gen_result["error"], 500)
            raw_pack_id = gen_result["pack_id"]
            logger.info("create_session 自定义背景模式: 已生成 pack_id=%s", raw_pack_id)
    # 关键分支：A2 起新建会话必须有剧本源，pack_id 和 background 都缺失时直接拒绝。
    if raw_pack_id is None:
        logger.warning("create_session 缺少剧本源: 未提供 pack_id 和 background")
        return None, error("NO_STORY_PACK", "未提供剧本包 ID 或背景描述，无法创建会话", 400)

    pack_id = str(raw_pack_id).strip()
    scenario_id = str(raw_scenario_id).strip() or "default"
    if not validate_character_id(pack_id) or not validate_character_id(scenario_id):
        logger.warning(
            "create_session 参数非法: pack_id=%s scenario_id=%s",
            pack_id,
            scenario_id,
        )
        return None, error("INVALID_ARGUMENT", "pack_id 或 scenario_id 格式非法", 400)
    # A2-Core 会话绑定只接受 registry 已校验通过的 pack，避免坏包进入持久化会话。
    context.story_pack_registry.refresh()
    bundle = context.story_pack_registry.get(pack_id)
    if bundle is None:
        logger.warning("create_session 剧本包不存在或未通过校验: pack_id=%s", pack_id)
        return None, error("PACK_NOT_FOUND", "pack_id 不存在或未通过校验", 404)
    if scenario_id != bundle.summary.scenario_id:
        logger.warning(
            "create_session scenario_id 不匹配: pack_id=%s scenario_id=%s expected=%s",
            pack_id,
            scenario_id,
            bundle.summary.scenario_id,
        )
        return None, error("PACK_NOT_FOUND", "scenario_id 不存在或未通过校验", 404)
    return (
        {
            "pack_id": bundle.summary.pack_id,
            "scenario_id": bundle.summary.scenario_id,
            "pack_version": bundle.summary.version,
            "compiled_artifact_hash": bundle.summary.compiled_artifact_hash,
            "start_scene_id": bundle.summary.start_scene_id,
        },
        None,
    )


def _parse_create_session_persona(
    body: dict[str, Any],
) -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    """
    功能：解析 create_session 的 persona_profile，并限制其必须为对象。
    入参：body（dict[str, Any]）：已解析 JSON 请求体。
    出参：tuple[dict[str, Any] | None, tuple[Any, int] | None]，成功返回 persona_profile，
        失败返回错误响应。
    异常：不抛异常；非对象 persona_profile 返回 INVALID_ARGUMENT。
    """
    persona_profile_raw = body.get("persona_profile", {})
    if persona_profile_raw is None:
        persona_profile_raw = {}
    if not isinstance(persona_profile_raw, dict):
        logger.warning("create_session 参数非法: persona_profile 不是对象")
        return None, error("INVALID_ARGUMENT", "persona_profile 必须是对象", 400)
    return dict(persona_profile_raw), None


def _build_create_session_draft(
    context: Any,
    character_id: str,
    sandbox_mode: bool,
    pack_metadata: dict[str, Any],
    persona_profile: dict[str, Any],
) -> _CreateSessionDraft:
    """
    功能：构造 create_session 持久化前的响应草稿和会话运行时身份。
    入参：context（Any）：API 运行时上下文；character_id（str）：基准角色 ID；
        sandbox_mode（bool）：是否启用沙盒；
        pack_metadata（dict[str, Any]）：已校验剧本元数据；
        persona_profile（dict[str, Any]）：人格配置。
    出参：_CreateSessionDraft，包含事务写入所需的冻结字段。
    异常：`build_initial_turn_payload` 异常向上抛出，避免返回不完整首屏状态。
    """
    created_at = now_iso()
    session_id = new_session_id()
    runtime_character_id = build_session_runtime_character_id(session_id)
    memory_policy = {"mode": "auto", "max_turns": DEFAULT_MEMORY_TURNS}
    initial_location_id = str(pack_metadata.get("start_scene_id") or "").strip() or None
    pack_id = str(pack_metadata.get("pack_id") or "").strip()
    bundle = context.story_pack_registry.get(pack_id) if pack_id else None
    initial_runtime = _build_initial_pack_runtime(bundle, runtime_character_id)
    response_payload: dict[str, Any] = {
        "session_id": session_id,
        "character_id": character_id,
        "runtime_character_id": runtime_character_id,
        "sandbox_mode": sandbox_mode,
        "current_session_turn_id": 0,
        "created_at": created_at,
    }
    response_payload.update(
        {
            "pack_id": pack_metadata.get("pack_id"),
            "scenario_id": pack_metadata.get("scenario_id"),
            "pack_version": pack_metadata.get("pack_version"),
            "compiled_artifact_hash": pack_metadata.get("compiled_artifact_hash"),
        }
    )
    if persona_profile:
        response_payload["persona_profile"] = persona_profile
    # 新会话首屏必须先有 GM 开场叙事，再把同一叙事生成的选项返回前端。
    initial_payload = build_initial_turn_payload(
        character_id,
        sandbox_mode,
        pack_id=pack_metadata.get("pack_id"),
        initial_location_id=initial_location_id,
    )
    active_character = initial_payload.get("active_character")
    if isinstance(active_character, dict):
        # 会话隔离边界：首屏展示的结构化角色状态也使用运行角色 ID，
        # 但上方 `character_id` 仍保留玩家选择的基准角色。
        initial_payload["active_character"] = {**active_character, "id": runtime_character_id}
    response_payload.update(initial_payload)
    _merge_initial_runtime_into_payload(
        context,
        response_payload,
        pack_id=pack_id or None,
        runtime_character_id=runtime_character_id,
        initial_runtime=initial_runtime,
    )
    return _CreateSessionDraft(
        session_id=session_id,
        character_id=character_id,
        runtime_character_id=runtime_character_id,
        sandbox_mode=sandbox_mode,
        created_at=created_at,
        memory_policy=memory_policy,
        initial_location_id=initial_location_id,
        initial_session_metadata=dict(initial_runtime.get("session_metadata") or {}),
        initial_state_flags=_unique_texts(initial_runtime.get("state_flags")),
        initial_granted_items=[
            item for item in initial_runtime.get("granted_items", []) if isinstance(item, dict)
        ],
        response_payload=response_payload,
    )


@sessions_blueprint.post("")
def create_session() -> tuple[Any, int]:
    """
    功能：创建会话并建立会话级并发控制与幂等索引。
    入参：HTTP JSON，请求体包含 request_id、character_id、sandbox_mode，
        并必须提供 pack_id 或 background。
    出参：tuple[Any, int]，成功返回 201 与会话元数据。
    异常：参数非法时返回 INVALID_ARGUMENT；缺少剧本源返回 NO_STORY_PACK；
        pack 不存在返回 PACK_NOT_FOUND；
        内部异常由 Flask 统一处理为 500。
    """
    body = parse_json_body()
    request_id, error_response = _validate_create_session_request_id(body)
    if error_response is not None:
        return error_response
    if request_id is None:
        raise AssertionError("request_id 校验成功后不应为空")

    context = get_runtime_context()
    # 幂等边界：同一 request_id 的历史创建结果优先于当前请求体语义校验，
    # 避免重复提交时后续 pack/persona 变化覆盖或阻断已冻结的会话绑定。
    cached_payload = _get_cached_create_session_response(context, request_id)
    if cached_payload is not None:
        logger.info("create_session 幂等预命中: request_id=%s", request_id)
        return success(cached_payload, status_code=201)

    character_id, error_response = _validate_create_session_character(body)
    if error_response is not None:
        return error_response
    if character_id is None:
        raise AssertionError("character_id 校验成功后不应为空")

    sandbox_mode = bool(body.get("sandbox_mode", False))
    pack_metadata, error_response = _resolve_create_session_pack_metadata(context, body)
    if error_response is not None:
        return error_response
    if pack_metadata is None:
        raise AssertionError("pack_metadata 校验成功后不应为空")

    persona_profile, error_response = _parse_create_session_persona(body)
    if error_response is not None:
        return error_response
    if persona_profile is None:
        raise AssertionError("persona_profile 校验成功后不应为空")

    draft = _build_create_session_draft(
        context,
        character_id,
        sandbox_mode,
        pack_metadata,
        persona_profile,
    )
    persisted_payload, created = context.session_store.create_session_with_idempotency(
        scope="create_session",
        request_id=request_id,
        session_id=draft.session_id,
        character_id=draft.character_id,
        sandbox_mode=draft.sandbox_mode,
        now_iso=draft.created_at,
        memory_policy=draft.memory_policy,
        response_payload=draft.response_payload,
        pack_metadata=pack_metadata,
        persona_profile=persona_profile,
        runtime_character_id=draft.runtime_character_id,
        initial_location_id=draft.initial_location_id,
        initial_session_metadata=draft.initial_session_metadata,
        initial_state_flags=draft.initial_state_flags,
        initial_granted_items=draft.initial_granted_items,
    )
    if not created:
        # 幂等边界：并发重放场景下事务内命中缓存，不重复创建会话。
        logger.info("create_session 幂等命中: request_id=%s", request_id)
        return success(persisted_payload, status_code=201)
    initialize_session_agent_memory(
        session_id=str(persisted_payload["session_id"]),
        context_dir=context.agent_context_dir,
    )
    logger.info(
        "create_session 创建成功: session_id=%s character_id=%s runtime_character_id=%s pack_id=%s",
        draft.session_id,
        draft.character_id,
        draft.runtime_character_id,
        pack_metadata.get("pack_id", ""),
    )
    return success(persisted_payload, status_code=201)


@sessions_blueprint.get("")
def list_sessions() -> tuple[Any, int]:
    """
    功能：列出已持久化的 Web 会话摘要，支持玩家从保存进度中选择继续。
    入参：HTTP query.limit（可选，1-100）：返回最近会话数量。
    出参：tuple[Any, int]，成功返回 items、total 与 limit。
    异常：limit 非法返回 INVALID_ARGUMENT；内部存储异常由 Flask 统一处理为 500。
    """
    limit, error_response = _parse_session_list_limit()
    if error_response is not None:
        return error_response
    if limit is None:
        raise AssertionError("limit 校验成功后不应为空")

    context = get_runtime_context()
    context.story_pack_registry.refresh()
    items = [
        _enrich_session_summary(context, summary)
        for summary in context.session_store.list_sessions(limit=limit)
    ]
    logger.info("list_sessions 查询成功: count=%s limit=%s", len(items), limit)
    return success({"items": items, "total": len(items), "limit": limit})


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
        "runtime_character_id": session.get("runtime_character_id", session["character_id"]),
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
            str(session.get("runtime_character_id") or session["character_id"]),
            bool(session["sandbox_mode"]),
            recent_memory=str(session.get("memory_summary", "")),
            pack_id=str(session.get("pack_id") or "") or None,
            session_metadata=session.get("session_metadata", {}),
        )
    )
    logger.info("get_session_detail 查询成功: session_id=%s", session_id)
    return success(payload)


@sessions_blueprint.delete("/<session_id>")
def delete_session_detail(session_id: str) -> tuple[Any, int]:
    """
    功能：删除指定 Web 会话及其私有运行数据。
    入参：session_id（path）：目标会话 ID。
    出参：tuple[Any, int]，成功返回删除摘要；不存在返回 404。
    异常：参数非法返回 INVALID_ARGUMENT；内部存储异常由 Flask 统一处理为 500。
    """
    if not validate_session_id(session_id):
        logger.warning("delete_session 参数非法: session_id=%s", session_id)
        return error("INVALID_ARGUMENT", "session_id 格式非法", 400)

    context = get_runtime_context()
    session_lock = context.get_session_lock(session_id)
    with session_lock:
        # 删除与回合执行、记忆刷新共享同一会话锁，确保不会边写回合边移除会话主记录。
        deleted = context.session_store.delete_session(session_id)
        if deleted is None:
            logger.warning("delete_session 会话不存在: session_id=%s", session_id)
            return error("SESSION_NOT_FOUND", "session_id 不存在", 404)
        deleted_agent_memory_path = delete_session_agent_memory(
            session_id=session_id,
            context_dir=context.agent_context_dir,
        )
    payload = {
        **deleted,
        "deleted_agent_memory": deleted_agent_memory_path is not None,
    }
    logger.info(
        "delete_session 删除成功: session_id=%s turns=%s memory_items=%s agent_memory=%s",
        session_id,
        payload.get("deleted_turns"),
        payload.get("deleted_memory_items"),
        payload.get("deleted_agent_memory"),
    )
    return success(payload)
