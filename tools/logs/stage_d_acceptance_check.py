from __future__ import annotations

import json
import uuid
from typing import Any

from flask import Flask

import web_api.service as web_service
from web_api import create_app


def _new_request_id(prefix: str) -> str:
    """
    功能：生成满足契约约束的请求幂等键。
    入参：prefix（str）：请求类型前缀，用于区分回归步骤。
    出参：str，格式为 `<prefix>_<20位十六进制>`，满足 `^[a-zA-Z0-9_-]{8,64}$`。
    异常：UUID 生成异常向上抛出；函数不做降级。
    """
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def _post_json(client: Any, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """
    功能：发送 JSON POST 请求并统一解析响应。
    入参：client（Any）：Flask test client；
        path（str）：请求路径；
        payload（dict[str, Any]）：JSON 请求体。
    出参：tuple[int, dict[str, Any]]，返回 HTTP 状态码与 JSON 响应体（解析失败时为空字典）。
    异常：请求发送异常向上抛出；JSON 解析失败时内部捕获并降级为空字典。
    """
    response = client.post(path, json=payload)
    try:
        body = response.get_json() or {}
    except Exception:  # noqa: BLE001
        body = {}
    return response.status_code, body


def _get_json(client: Any, path: str) -> tuple[int, dict[str, Any]]:
    """
    功能：发送 GET 请求并统一解析响应。
    入参：client（Any）：Flask test client；path（str）：请求路径。
    出参：tuple[int, dict[str, Any]]，返回 HTTP 状态码与 JSON 响应体（解析失败时为空字典）。
    异常：请求发送异常向上抛出；JSON 解析失败时内部捕获并降级为空字典。
    """
    response = client.get(path)
    try:
        body = response.get_json() or {}
    except Exception:  # noqa: BLE001
        body = {}
    return response.status_code, body


def _disable_gm_llm(app: Flask) -> None:
    """
    功能：在验收脚本中显式关闭 GM 真实 LLM，固定走 deterministic 渲染，避免超时噪声干扰回归结论。
    入参：app（Flask）：已完成 runtime 初始化的应用实例。
    出参：None。
    异常：当运行时上下文缺失关键对象时抛出 RuntimeError；不做静默降级。
    """
    runtime = app.extensions.get("tre_api_context")
    if runtime is None or getattr(runtime, "main_loop", None) is None:
        raise RuntimeError("tre_api_context 未初始化，无法执行阶段 D 验收。")
    runtime.main_loop.gm_agent.llm_enabled = False


def _assert_status(status: int, expected: int, body: dict[str, Any], step: str) -> None:
    """
    功能：统一断言接口状态码，失败时提供可读错误上下文。
    入参：status（int）：实际状态码；expected（int）：期望状态码；
        body（dict[str, Any]）：响应体；step（str）：当前回归步骤标识。
    出参：None。
    异常：状态码不匹配时抛出 AssertionError，包含步骤与响应体，便于定位。
    """
    assert status == expected, f"{step} 失败：status={status}, body={body}"


def _run_contract_and_e2e() -> dict[str, Any]:
    """
    功能：执行阶段 D 的契约接口回归与端到端剧本（5 回合 + discard + commit）。
    入参：无。
    出参：dict[str, Any]，包含契约步骤状态、剧本回合号与关键断言结果。
    异常：任一步骤失败时抛出 AssertionError；不在函数内吞错误。
    """
    app = create_app()
    _disable_gm_llm(app)
    client = app.test_client()

    report: dict[str, Any] = {"contract": {}, "e2e": {}}

    status, body = _post_json(
        client,
        "/api/sessions",
        {
            "request_id": _new_request_id("reqcs"),
            "character_id": "player_01",
            "sandbox_mode": False,
        },
    )
    _assert_status(status, 201, body, "create_session")
    session_id = str(body["session_id"])
    report["contract"]["create_session"] = {"status": status, "session_id": session_id}

    status, body = _get_json(client, f"/api/sessions/{session_id}")
    _assert_status(status, 200, body, "get_session")
    report["contract"]["get_session"] = {
        "status": status,
        "current_session_turn_id": body["current_session_turn_id"],
    }

    five_turn_ids: list[int] = []
    for idx in range(5):
        status, body = _post_json(
            client,
            f"/api/sessions/{session_id}/turns",
            {
                "request_id": _new_request_id(f"reqt{idx}"),
                "user_input": "观察周围" if idx % 2 == 0 else "尝试互动",
                "character_id": "player_01",
                "memory": {"mode": "auto", "max_turns": 20},
            },
        )
        _assert_status(status, 200, body, f"create_turn_{idx + 1}")
        five_turn_ids.append(int(body["session_turn_id"]))

    status, body = _get_json(client, f"/api/sessions/{session_id}/turns?page=1&page_size=20")
    _assert_status(status, 200, body, "list_turns")
    assert int(body["total"]) >= 5, f"list_turns total 异常：{body}"
    report["contract"]["list_turns"] = {"status": status, "total": body["total"]}

    status, body = _get_json(client, f"/api/sessions/{session_id}/turns/{five_turn_ids[-1]}")
    _assert_status(status, 200, body, "get_turn")
    report["contract"]["get_turn"] = {
        "status": status,
        "session_turn_id": body["session_turn_id"],
    }

    status, body = _get_json(client, f"/api/sessions/{session_id}/memory?format=summary")
    _assert_status(status, 200, body, "get_memory")
    report["contract"]["get_memory"] = {
        "status": status,
        "summary_len": len(str(body.get("summary", ""))),
    }

    status, body = _post_json(
        client,
        f"/api/sessions/{session_id}/memory/refresh",
        {"request_id": _new_request_id("reqmr"), "max_turns": 20},
    )
    _assert_status(status, 200, body, "refresh_memory")
    report["contract"]["refresh_memory"] = {
        "status": status,
        "covered_turn_range": body.get("covered_turn_range", {}),
    }

    status, body = _post_json(
        client,
        f"/api/sessions/{session_id}/sandbox/discard",
        {"request_id": _new_request_id("reqsd")},
    )
    _assert_status(status, 200, body, "sandbox_discard")
    discard_turn_id = int(body["session_turn_id"])
    report["contract"]["sandbox_discard"] = {
        "status": status,
        "session_turn_id": discard_turn_id,
    }

    status, body = _post_json(
        client,
        f"/api/sessions/{session_id}/sandbox/commit",
        {"request_id": _new_request_id("reqsc")},
    )
    _assert_status(status, 200, body, "sandbox_commit")
    commit_turn_id = int(body["session_turn_id"])
    report["contract"]["sandbox_commit"] = {
        "status": status,
        "session_turn_id": commit_turn_id,
    }

    status, body = _post_json(
        client,
        f"/api/sessions/{session_id}/reset",
        {"request_id": _new_request_id("reqrs"), "keep_character": True},
    )
    _assert_status(status, 200, body, "reset_session")
    report["contract"]["reset"] = {
        "status": status,
        "current_session_turn_id": body["current_session_turn_id"],
    }

    report["e2e"] = {
        "session_id": session_id,
        "five_turn_ids": five_turn_ids,
        "discard_turn_id": discard_turn_id,
        "commit_turn_id": commit_turn_id,
    }
    return report


def _run_restart_recovery() -> dict[str, Any]:
    """
    功能：执行重启恢复测试：创建会话并完成 1 回合后，重建 Flask app 再继续同一 session 回合。
    入参：无。
    出参：dict[str, Any]，包含重启前后回合游标与继续游玩的回合号。
    异常：会话恢复失败或回合未递增时抛出 AssertionError。
    """
    app_before = create_app()
    _disable_gm_llm(app_before)
    client_before = app_before.test_client()

    status, body = _post_json(
        client_before,
        "/api/sessions",
        {
            "request_id": _new_request_id("reqra"),
            "character_id": "player_01",
            "sandbox_mode": False,
        },
    )
    _assert_status(status, 201, body, "restart_create_session")
    session_id = str(body["session_id"])

    status, body = _post_json(
        client_before,
        f"/api/sessions/{session_id}/turns",
        {
            "request_id": _new_request_id("reqrb1"),
            "user_input": "观察周围",
            "character_id": "player_01",
        },
    )
    _assert_status(status, 200, body, "restart_turn_before")
    first_turn_id = int(body["session_turn_id"])

    app_after = create_app()
    _disable_gm_llm(app_after)
    client_after = app_after.test_client()

    status, body = _get_json(client_after, f"/api/sessions/{session_id}")
    _assert_status(status, 200, body, "restart_get_session_after")
    loaded_cursor = int(body["current_session_turn_id"])

    status, body = _post_json(
        client_after,
        f"/api/sessions/{session_id}/turns",
        {
            "request_id": _new_request_id("reqrb2"),
            "user_input": "尝试互动",
            "character_id": "player_01",
        },
    )
    _assert_status(status, 200, body, "restart_turn_after")
    second_turn_id = int(body["session_turn_id"])
    assert second_turn_id >= first_turn_id + 1, (
        f"重启后回合未推进：first={first_turn_id}, second={second_turn_id}"
    )

    status, body = _get_json(client_after, f"/api/sessions/{session_id}")
    _assert_status(status, 200, body, "restart_get_session_final")
    final_cursor = int(body["current_session_turn_id"])
    assert final_cursor >= second_turn_id, (
        f"重启后会话游标异常：final={final_cursor}, second={second_turn_id}"
    )

    return {
        "session_id": session_id,
        "turn_id_before_restart": first_turn_id,
        "loaded_after_restart": loaded_cursor,
        "turn_id_after_restart_play": second_turn_id,
        "session_turn_cursor_after_restart": final_cursor,
    }


def main() -> None:
    """
    功能：阶段 D 最小可玩验收入口，执行契约回归、端到端剧本与重启恢复并输出结构化证据。
    入参：无。
    出参：None；标准输出打印验收标记与 JSON 结果。
    异常：任一验收步骤失败时抛出 AssertionError 并返回非 0 退出码。
    """
    # 验收脚本允许更长回合窗口，避免真实模型渲染的偶发耗时导致误判。
    web_service.TURN_TIMEOUT_SECONDS = 60
    report = _run_contract_and_e2e()
    report["restart"] = _run_restart_recovery()
    print("STAGE_D_ACCEPTANCE_OK")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
