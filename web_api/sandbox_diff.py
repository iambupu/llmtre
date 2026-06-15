"""
A3 Active/Shadow 沙盒差异摘要生成器。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from state.contracts.sandbox import SandboxDiffMode, SandboxDiffSummary, SandboxFieldChange


@dataclass(frozen=True)
class _SandboxDiffGroups:
    """
    功能：承载沙盒差异的三类展示分组。
    入参：character/inventory/world（list[SandboxFieldChange]）：角色、背包和世界状态差异。
    出参：_SandboxDiffGroups。
    异常：dataclass 构造不做业务校验，调用方负责传入列表。
    """

    character: list[SandboxFieldChange]
    inventory: list[SandboxFieldChange]
    world: list[SandboxFieldChange]


@dataclass(frozen=True)
class _TableCompareSpec:
    """
    功能：描述一组 Active/Shadow 表比较规格。
    入参：active_table/shadow_table（str）：表名；key_fields/compare_fields（list[str]）：主键与比较字段。
    出参：_TableCompareSpec。
    异常：dataclass 构造不做业务校验，SQL 前会检查表与字段是否存在。
    """

    active_table: str
    shadow_table: str
    key_fields: list[str]
    compare_fields: list[str]


@dataclass(frozen=True)
class _RowChangeSpec:
    """
    功能：描述同一对象一组字段差异的展示规格。
    入参：subject_id（str）：对象 ID；active/shadow（dict）：两侧行；fields（list[str]）：比较字段；
        source_table/label_prefix（str）：来源表组与展示前缀。
    出参：_RowChangeSpec。
    异常：dataclass 构造不做业务校验，字段缺失按 None 比较。
    """

    subject_id: str
    active: dict[str, Any]
    shadow: dict[str, Any]
    fields: list[str]
    source_table: str
    label_prefix: str


def build_sandbox_diff_summary(
    *,
    db_path: str,
    session_id: str,
    trace_id: str,
    mode: SandboxDiffMode,
) -> dict[str, Any]:
    """
    功能：读取 SQLite Active/Shadow 表并生成沙盒差异摘要。
    入参：db_path（str）：运行时 SQLite 路径；session_id（str）：当前会话 ID；
        trace_id（str）：请求追踪 ID；mode（SandboxDiffMode）：preview/committed/discarded。
    出参：dict[str, Any]，符合 SandboxDiffSummary JSON 契约。
    异常：不向外抛业务异常；数据库不可读时返回 diagnostics。
    """
    diagnostics: list[str] = []
    character_changes: list[SandboxFieldChange] = []
    inventory_changes: list[SandboxFieldChange] = []
    world_changes: list[SandboxFieldChange] = []
    if not db_path or not Path(db_path).exists():
        diagnostics.append("无法生成沙盒差异：运行时数据库路径不可用")
        return _summary(
            session_id=session_id,
            trace_id=trace_id,
            mode=mode,
            groups=_SandboxDiffGroups(character=[], inventory=[], world=[]),
            diagnostics=diagnostics,
        )
    try:
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            character_changes = _compare_entity_tables(connection, diagnostics)
            inventory_changes = _compare_inventory_tables(connection, diagnostics)
            world_changes = _compare_world_tables(connection, diagnostics)
    except sqlite3.Error as exc:
        diagnostics.append(f"沙盒差异读取失败: {exc}")

    return _summary(
        session_id=session_id,
        trace_id=trace_id,
        mode=mode,
        groups=_SandboxDiffGroups(
            character=character_changes,
            inventory=inventory_changes,
            world=world_changes,
        ),
        diagnostics=diagnostics,
    )


def build_unavailable_sandbox_diff_summary(
    *,
    session_id: str,
    trace_id: str,
    mode: SandboxDiffMode,
    reason: str,
) -> dict[str, Any]:
    """
    功能：在缺少可比较数据库时构造显式降级差异摘要。
    入参：session_id（str）：会话 ID；trace_id（str）：追踪 ID；
        mode（SandboxDiffMode）：摘要阶段；reason（str）：降级原因。
    出参：dict[str, Any]，has_changes 为 False 且 diagnostics 包含 reason。
    异常：不抛异常。
    """
    return _summary(
        session_id=session_id,
        trace_id=trace_id,
        mode=mode,
        groups=_SandboxDiffGroups(character=[], inventory=[], world=[]),
        diagnostics=[reason],
    )


def _summary(
    *,
    session_id: str,
    trace_id: str,
    mode: SandboxDiffMode,
    groups: _SandboxDiffGroups,
    diagnostics: list[str],
) -> dict[str, Any]:
    """
    功能：组装 SandboxDiffSummary，并根据分组变化计算 has_changes。
    入参：session_id/trace_id/mode：摘要元信息；
        groups（_SandboxDiffGroups）：已生成差异；diagnostics（list[str]）：降级说明。
    出参：dict[str, Any]。
    异常：Pydantic 字段异常向上抛出，表示调用方传入类型错误。
    """
    has_changes = bool(groups.character or groups.inventory or groups.world)
    return SandboxDiffSummary(
        session_id=session_id,
        trace_id=trace_id,
        mode=mode,
        has_changes=has_changes,
        character_changes=groups.character,
        inventory_changes=groups.inventory,
        world_changes=groups.world,
        diagnostics=diagnostics,
    ).model_dump(mode="json")


def _compare_entity_tables(
    connection: sqlite3.Connection,
    diagnostics: list[str],
) -> list[SandboxFieldChange]:
    """
    功能：比较 entities_active/entities_shadow 中角色资源、位置和状态标签差异。
    入参：connection（sqlite3.Connection）：只读连接；diagnostics（list[str]）：诊断累积器。
    出参：list[SandboxFieldChange]。
    异常：SQL 异常向上抛出，由总入口转换为 diagnostics。
    """
    rows = _paired_rows(
        connection,
        spec=_TableCompareSpec(
            active_table="entities_active",
            shadow_table="entities_shadow",
            key_fields=["entity_id"],
            compare_fields=[
                "hp",
                "max_hp",
                "mp",
                "max_mp",
                "current_location_id",
                "state_flags_json",
            ],
        ),
        diagnostics=diagnostics,
    )
    changes: list[SandboxFieldChange] = []
    for subject_id, active, shadow in rows:
        changes.extend(
            _row_field_changes(
                _RowChangeSpec(
                    subject_id=subject_id,
                    active=active,
                    shadow=shadow,
                    fields=[
                        "hp",
                        "max_hp",
                        "mp",
                        "max_mp",
                        "current_location_id",
                        "state_flags_json",
                    ],
                    source_table="entities",
                    label_prefix="角色变化",
                )
            )
        )
    return changes


def _compare_inventory_tables(
    connection: sqlite3.Connection,
    diagnostics: list[str],
) -> list[SandboxFieldChange]:
    """
    功能：比较 inventory_active/inventory_shadow 中物品数量差异。
    入参：connection（sqlite3.Connection）：只读连接；diagnostics（list[str]）：诊断累积器。
    出参：list[SandboxFieldChange]。
    异常：SQL 异常向上抛出，由总入口转换为 diagnostics。
    """
    rows = _paired_rows(
        connection,
        spec=_TableCompareSpec(
            active_table="inventory_active",
            shadow_table="inventory_shadow",
            key_fields=["owner_id", "item_id"],
            compare_fields=["quantity"],
        ),
        diagnostics=diagnostics,
    )
    changes: list[SandboxFieldChange] = []
    for subject_id, active, shadow in rows:
        changes.extend(
            _row_field_changes(
                _RowChangeSpec(
                    subject_id=subject_id,
                    active=active,
                    shadow=shadow,
                    fields=["quantity"],
                    source_table="inventory",
                    label_prefix="背包变化",
                )
            )
        )
    return changes


def _compare_world_tables(
    connection: sqlite3.Connection,
    diagnostics: list[str],
) -> list[SandboxFieldChange]:
    """
    功能：比较 world_state_active/world_state_shadow 中世界状态差异。
    入参：connection（sqlite3.Connection）：只读连接；diagnostics（list[str]）：诊断累积器。
    出参：list[SandboxFieldChange]。
    异常：SQL 异常向上抛出，由总入口转换为 diagnostics。
    """
    rows = _paired_rows(
        connection,
        spec=_TableCompareSpec(
            active_table="world_state_active",
            shadow_table="world_state_shadow",
            key_fields=["key"],
            compare_fields=["value_json"],
        ),
        diagnostics=diagnostics,
    )
    changes: list[SandboxFieldChange] = []
    for subject_id, active, shadow in rows:
        changes.extend(
            _row_field_changes(
                _RowChangeSpec(
                    subject_id=subject_id,
                    active=active,
                    shadow=shadow,
                    fields=["value_json"],
                    source_table="world_state",
                    label_prefix="世界标记",
                )
            )
        )
    return changes


def _paired_rows(
    connection: sqlite3.Connection,
    *,
    spec: _TableCompareSpec,
    diagnostics: list[str],
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """
    功能：按主键把 Active/Shadow 两张表配对，供各分组比较复用。
    入参：connection（sqlite3.Connection）：只读连接；spec（_TableCompareSpec）：表比较规格；
        diagnostics（list[str]）：诊断累积器。
    出参：list[tuple[str, dict, dict]]，subject_id 与 Active/Shadow 行。
    异常：SQL 异常向上抛出；缺表/缺字段时追加 diagnostics 并返回空列表。
    """
    active_columns = _table_columns(connection, spec.active_table)
    shadow_columns = _table_columns(connection, spec.shadow_table)
    if not active_columns or not shadow_columns:
        diagnostics.append(f"沙盒差异跳过 {spec.active_table}/{spec.shadow_table}：表不存在")
        return []
    required = set(spec.key_fields + spec.compare_fields)
    missing_active = required - active_columns
    missing_shadow = required - shadow_columns
    if missing_active or missing_shadow:
        diagnostics.append(
            f"沙盒差异跳过 {spec.active_table}/{spec.shadow_table}：缺字段 "
            f"active={sorted(missing_active)} shadow={sorted(missing_shadow)}"
        )
        return []
    active_rows = _rows_by_key(connection, spec.active_table, spec.key_fields, spec.compare_fields)
    shadow_rows = _rows_by_key(connection, spec.shadow_table, spec.key_fields, spec.compare_fields)
    paired: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for key in sorted(set(active_rows) | set(shadow_rows)):
        paired.append((key, active_rows.get(key, {}), shadow_rows.get(key, {})))
    return paired


def _rows_by_key(
    connection: sqlite3.Connection,
    table: str,
    key_fields: list[str],
    compare_fields: list[str],
) -> dict[str, dict[str, Any]]:
    """
    功能：读取表数据并按组合 key 转为字典。
    入参：connection（sqlite3.Connection）：只读连接；table（str）：表名；
        key_fields（list[str]）：主键字段；compare_fields（list[str]）：比较字段。
    出参：dict[str, dict[str, Any]]。
    异常：SQL 异常向上抛出。
    """
    fields = key_fields + compare_fields
    sql = f"SELECT {', '.join(fields)} FROM {table}"
    rows: dict[str, dict[str, Any]] = {}
    for row in connection.execute(sql).fetchall():
        payload = {field: _normalize_value(row[field]) for field in fields}
        key = ":".join(str(payload[field]) for field in key_fields)
        rows[key] = payload
    return rows


def _row_field_changes(spec: _RowChangeSpec) -> list[SandboxFieldChange]:
    """
    功能：比较同一对象的一组字段并生成差异项。
    入参：spec（_RowChangeSpec）：对象、两侧行、字段列表与展示规格。
    出参：list[SandboxFieldChange]。
    异常：不抛异常。
    """
    changes: list[SandboxFieldChange] = []
    for field in spec.fields:
        before = spec.active.get(field)
        after = spec.shadow.get(field)
        if before == after:
            continue
        changes.append(
            SandboxFieldChange(
                subject_id=spec.subject_id,
                field=field,
                before=before,
                after=after,
                label=f"{spec.label_prefix}：{spec.subject_id}.{field}",
                source={"table": spec.source_table},
            )
        )
    return changes


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    """
    功能：读取表字段集合。
    入参：connection（sqlite3.Connection）：只读连接；table（str）：表名。
    出参：set[str]，表不存在时为空集合。
    异常：不抛异常；PRAGMA 失败时返回空集合。
    """
    try:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return set()
    return {str(row["name"]) for row in rows}


def _normalize_value(value: Any) -> Any:
    """
    功能：把 JSON 文本字段规整为结构值，其他字段原样返回。
    入参：value（Any）：SQLite 字段值。
    出参：Any，JSON 对象/数组可解析时返回解析结果。
    异常：JSON 解析失败时返回原字符串。
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value
