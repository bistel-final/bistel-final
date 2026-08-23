"""`V5-CM-2.6` 전환 계약 단위 테스트.

DB에 붙지 않는다. `TargetInventory`를 직접 만들어 순수 판정만 고정한다. 실제 catalog
읽기와 transaction은 `test_rehearsal_container.py`가 `container` marker로 검증한다.

상수는 Gate 0 실측에서 왔으므로, 테스트도 그 실측값을 그대로 쓴다.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import postgres_transition as transition  # noqa: E402

LEGACY_ALARMS = {"trace_alarm_history": 126, "summary_alarm_history": 47}


@pytest.fixture(autouse=True)
def _pin_base_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """축소 fixture는 실제 catalog 127 column을 만들 수 없다.

    `TargetInventory`에 test 전용 우회 field를 두면 production adapter가 잘못 채울 수
    있으므로(구현리뷰 3차 권장 1) field 대신 **산식 함수**를 여기서만 대체한다.
    """

    def pinned(inventory: transition.TargetInventory) -> str:
        wafer = {
            inventory.column_types.get(name, {}).get("wafer")
            for name in transition.WAFER_ALTER_TABLES
        }
        return (
            transition.FINAL_BASE_CATALOG_SHA256
            if wafer == {transition.FINAL_WAFER_TYPE}
            else transition.LEGACY_BASE_CATALOG_SHA256
        )

    monkeypatch.setattr(transition, "base_catalog_sha256", pinned)


FINAL_ALARMS = {"trace_alarm_history": 138, "summary_alarm_history": 51}


def _inventory(
    database: str = "kosa_agent",
    *,
    wafer_type: str = transition.LEGACY_WAFER_TYPE,
    action_rows: int | None = None,
    rag_rows: int | None = None,
    alarms: Mapping[str, int] | None = None,
    drop_tables: tuple[str, ...] = (),
    extra_tables: tuple[str, ...] = (),
    extra_sequences: tuple[str, ...] = (),
    views: tuple[str, ...] = (transition.LEGACY_VIEW,),
    other: tuple[str, ...] = (),
    extensions: tuple[str, ...] = ("plpgsql", "vector"),
    fks: tuple[transition.ForeignKeyTuple, ...] | None = None,
    fk_child_rows: Mapping[str, int] | None = None,
    server_major: int = 16,
    view_sha: str | None = None,
    view_owner: str | None = transition.LEGACY_VIEW_OWNER,
    view_acl: str | None = transition.LEGACY_VIEW_ACL,
    view_comment: str | None = transition.LEGACY_VIEW_COMMENT,
    indexes: Mapping[str, Mapping[str, str]] | None = None,
    constraints: Mapping[str, tuple[tuple[str, str], ...]] | None = None,
    rag_live: str | None = "f" * 64,
    rag_embedding: str | None = "e" * 64,
    column_details: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    is_superuser: bool | None = True,
    owner_match: bool | None = True,
    role_name: str | None = transition.LEGACY_VIEW_OWNER,
    base_content: Mapping[str, str] | None = None,
) -> transition.TargetInventory:
    profile = transition.TARGET_PROFILE.get(database, "runtime")
    tables, sequences = transition.expected_relations(
        transition.TargetInventory(
            database=database,
            profile=profile,
            server_major=server_major,
            tables=(),
            views=(),
            sequences=(),
            other_relations=(),
            extensions=(),
        )
    )
    table_names = tuple(sorted((set(tables) - set(drop_tables)) | set(extra_tables)))
    sequence_names = tuple(sorted(set(sequences) | set(extra_sequences)))

    is_final = wafer_type == transition.FINAL_WAFER_TYPE
    counts: dict[str, int] = dict(
        transition.final_base_rows(profile)
        if is_final
        else transition.LEGACY_BASE_ROWS[profile]
    )
    counts.update(alarms or ({} if is_final else LEGACY_ALARMS))
    if action_rows is not None:
        counts["action_history"] = action_rows
    for name in transition.RAG_TABLES:
        counts.setdefault(name, 3 if rag_rows is None else rag_rows)
    for name in transition.PRESERVED_TABLES_BY_PROFILE[profile]:
        counts.setdefault(name, 0)
    for name in transition.LEGACY_HANDOFF_TABLES_BY_TARGET.get(database, ()):
        counts.setdefault(name, 0)
    counts.update(fk_child_rows or {})

    column_types = {
        name: {"wafer": wafer_type} for name in transition.WAFER_ALTER_TABLES
    }
    if column_details is None:
        column_details = {
            name: {"wafer": {"data_type": wafer_type, "is_nullable": "YES"}}
            for name in transition.WAFER_ALTER_TABLES
        }
    if fks is None:
        fks = transition.EXPECTED_EXTERNAL_FKS_BY_PROFILE[profile]
    watched = (
        *transition.PRESERVED_TABLES_BY_PROFILE[profile],
        *transition.RAG_TABLES,
        *transition.LEGACY_HANDOFF_TABLES_BY_TARGET.get(database, ()),
    )
    if indexes is None:
        indexes = {name: {f"{name}_pkey": f"INDEX {name}"} for name in watched}
        for name in transition.LEGACY_HANDOFF_INDEXES_BY_TARGET.get(database, ()):
            indexes.setdefault("document_corpus", {})[name] = f"INDEX {name}"
    if constraints is None:
        constraints = {name: ((f"{name}_pkey", "p"),) for name in watched}
    return transition.TargetInventory(
        database=database,
        profile=profile,
        server_major=server_major,
        tables=table_names,
        views=views,
        sequences=sequence_names,
        other_relations=other,
        extensions=extensions,
        row_counts=counts,
        column_types=column_types,
        external_fks=fks,
        view_sha256=transition.LEGACY_VIEW_SHA256 if view_sha is None else view_sha,
        view_owner=view_owner,
        view_acl=view_acl,
        view_comment=view_comment,
        indexes={k: dict(v) for k, v in indexes.items()},
        constraints={k: tuple(v) for k, v in constraints.items()},
        rag_live_fingerprint=rag_live,
        rag_embedding_projection=rag_embedding,
        column_details={
            k: {c: dict(v) for c, v in cols.items()}
            for k, cols in column_details.items()
        },
        is_superuser=is_superuser,
        owner_match=owner_match,
        role_name=role_name,
        base_content=(
            dict(base_content)
            if base_content is not None
            else (
                transition.final_base_content(profile)
                if wafer_type == transition.FINAL_WAFER_TYPE
                else {name: ("b" * 64) for name in transition.BASE_TABLES}
            )
        ),
    )


# ---------------------------------------------------------------------------
# Gate 0 실측이 상수로 고정됐는가
# ---------------------------------------------------------------------------


def test_target_order_and_profiles_match_wbs() -> None:
    assert transition.ORDERED_TARGETS == (
        "kosa_agent_e2e",
        "kosa_agent",
        "kosa_text2sql",
    )
    assert transition.TARGET_PROFILE["kosa_agent"] == "runtime"
    assert transition.TARGET_PROFILE["kosa_agent_e2e"] == "runtime"
    assert transition.TARGET_PROFILE["kosa_text2sql"] == "evaluation"


def test_external_fk_expectation_is_split_by_profile() -> None:
    """`kosa_text2sql`에는 action_history를 참조하는 세 table 자체가 없다.

    공통 5건으로 요구하면 세 번째 target이 항상 중단되어 순차 적용이 완주하지 못한다
    (5차 계획리뷰 필수 1).
    """

    runtime = transition.EXPECTED_EXTERNAL_FKS_BY_PROFILE["runtime"]
    evaluation = transition.EXPECTED_EXTERNAL_FKS_BY_PROFILE["evaluation"]
    assert len(runtime) == 5
    assert len(evaluation) == 2
    assert set(evaluation) < set(runtime)
    pairs = {(child, parent) for _n, child, _cc, parent, _pc, _d, _u in runtime}
    assert ("action_delivery", "action_history") in pairs
    eval_pairs = {(child, parent) for _n, child, _cc, parent, _pc, _d, _u in evaluation}
    assert ("action_delivery", "action_history") not in eval_pairs
    # 이름·컬럼·action까지 실측과 exact여야 한다(구현리뷰 1차 필수 4).
    assert (
        "action_delivery_action_id_fkey",
        "action_delivery",
        ("action_id",),
        "action_history",
        ("action_id",),
        "a",
        "a",
    ) in runtime


def test_wafer_alter_targets_are_exactly_four() -> None:
    assert transition.WAFER_ALTER_TABLES == (
        "evaluation",
        "summary_alarm_history",
        "summary_data",
        "trace_alarm_history",
    )
    assert transition.LEGACY_WAFER_TYPE == "smallint"


def test_preserved_projection_counts_match_gate0() -> None:
    assert len(transition.PRESERVED_TABLES_BY_PROFILE["runtime"]) == 11
    assert len(transition.PRESERVED_TABLES_BY_PROFILE["evaluation"]) == 2
    assert len(transition.PRESERVED_SEQUENCES_BY_PROFILE["runtime"]) == 3
    assert len(transition.PRESERVED_SEQUENCES_BY_PROFILE["evaluation"]) == 1
    # `kosa_text2sql`의 RAG 2종은 구 epoch(PR #48) 형상이라 B 관리 대상이 아니다.
    # B가 "지워도 된다"고 했지만 2.6은 보존만 하고 넘긴다(2026-08-22 확인).
    assert transition.LEGACY_HANDOFF_TABLES_BY_TARGET["kosa_text2sql"] == (
        "document",
        "document_chunk",
        "document_corpus",
    )
    assert "kosa_agent" not in transition.LEGACY_HANDOFF_TABLES_BY_TARGET
    assert transition.B_MANAGED_RAG_TARGETS == frozenset(
        {"kosa_agent", "kosa_agent_e2e"}
    )


def test_legacy_and_final_row_expectations() -> None:
    assert transition.LEGACY_ROW_COUNTS["runtime"]["action_history"] == 0
    assert transition.LEGACY_ROW_COUNTS["evaluation"]["action_history"] == 48
    assert transition.FINAL_ACTION_ROWS == {"runtime": 0, "evaluation": 12}
    assert transition.COMPAT_VIEW_ROWS == 189


# ---------------------------------------------------------------------------
# 상태 판정
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("database", transition.ORDERED_TARGETS)
def test_gate0_shape_classifies_as_base_legacy_epoch(database: str) -> None:
    assert transition.classify_target(_inventory(database)) is (
        transition.BaseState.BASE_LEGACY_EPOCH
    )


def _final(
    database: str = "kosa_agent", **overrides: Any
) -> transition.TargetInventory:
    """전환이 끝난 target의 **실제** 형상.

    final row/type에 legacy View를 붙인 fixture는 현실에 없는 조합이라, 상태별 View
    분기 결함을 통과시킨다(구현리뷰 5차 필수 2).
    """

    profile = transition.TARGET_PROFILE[database]
    kwargs: dict[str, Any] = {
        "wafer_type": transition.FINAL_WAFER_TYPE,
        "action_rows": transition.FINAL_ACTION_ROWS[profile],
        "alarms": FINAL_ALARMS,
        "view_sha": transition.COMPAT_VIEW_SHA256,
    }
    kwargs.update(overrides)
    return _inventory(database, **kwargs)


@pytest.mark.parametrize("database", transition.ORDERED_TARGETS)
def test_final_shape_classifies_as_final_adopted(database: str) -> None:
    assert transition.classify_target(_final(database)) is (
        transition.BaseState.FINAL_ADOPTED
    )


@pytest.mark.parametrize("database", transition.ORDERED_TARGETS)
def test_final_base_with_legacy_view_is_not_a_valid_state(database: str) -> None:
    """전환이 중간에 멈춘 형상 — 행은 final인데 View를 되돌리지 않았다."""

    with pytest.raises(transition.TransitionError) as caught:
        transition.classify_target(
            _final(database, view_sha=transition.LEGACY_VIEW_SHA256)
        )
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


@pytest.mark.parametrize("database", transition.ORDERED_TARGETS)
def test_legacy_base_with_compat_view_is_not_a_valid_state(database: str) -> None:
    """반대 방향 — View만 바꾸고 데이터를 두고 온 형상."""

    with pytest.raises(transition.TransitionError) as caught:
        transition.classify_target(
            _inventory(database, view_sha=transition.COMPAT_VIEW_SHA256)
        )
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


def test_mixed_wafer_types_are_drift() -> None:
    inventory = _inventory()
    mixed = dict(inventory.column_types)
    mixed["evaluation"] = {"wafer": transition.FINAL_WAFER_TYPE}
    drifted = transition.TargetInventory(
        **{**inventory.__dict__, "column_types": mixed}
    )
    assert transition.classify_base(drifted) is transition.BaseState.PARTIAL_OR_DRIFT


@pytest.mark.parametrize(
    "alarms",
    [
        {"trace_alarm_history": 138, "summary_alarm_history": 47},
        {"trace_alarm_history": 126, "summary_alarm_history": 51},
        {"trace_alarm_history": 0, "summary_alarm_history": 0},
    ],
)
def test_partial_alarm_counts_are_drift(alarms: dict[str, int]) -> None:
    with pytest.raises(transition.TransitionError) as caught:
        transition.classify_target(_inventory(alarms=alarms))
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


def test_evaluation_action_48_is_legacy_and_12_is_final() -> None:
    legacy = _inventory("kosa_text2sql", action_rows=48)
    assert transition.classify_target(legacy) is (
        transition.BaseState.BASE_LEGACY_EPOCH
    )
    final = _final("kosa_text2sql", action_rows=12)
    assert transition.classify_target(final) is transition.BaseState.FINAL_ADOPTED
    with pytest.raises(transition.TransitionError):
        transition.classify_target(_inventory("kosa_text2sql", action_rows=12))


# ---------------------------------------------------------------------------
# 보존·RAG·FK·View precondition — 각 방어가 실제로 판정하는가
# ---------------------------------------------------------------------------


def test_missing_preserved_table_is_refused() -> None:
    """보존 대상이 사라진 것도 drift다. "base 9 + RAG면 통과"가 아니다."""

    with pytest.raises(transition.TransitionError) as caught:
        transition.classify_target(_inventory(drop_tables=("agent_run",)))
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


def test_unknown_table_is_refused() -> None:
    with pytest.raises(transition.TransitionError):
        transition.classify_target(_inventory(extra_tables=("scratch_table",)))


def test_unknown_sequence_is_refused() -> None:
    with pytest.raises(transition.TransitionError):
        transition.classify_target(_inventory(extra_sequences=("scratch_seq",)))


def test_unexpected_view_or_relation_is_refused() -> None:
    with pytest.raises(transition.TransitionError):
        transition.classify_target(
            _inventory(views=(transition.LEGACY_VIEW, "v_extra"))
        )
    with pytest.raises(transition.TransitionError):
        transition.classify_target(_inventory(other=("some_foreign_table",)))


def test_handoff_table_belongs_only_to_text2sql() -> None:
    """`document_corpus`는 evaluation target에서만 허용된다."""

    assert transition.classify_target(_inventory("kosa_text2sql")) is (
        transition.BaseState.BASE_LEGACY_EPOCH
    )
    with pytest.raises(transition.TransitionError):
        transition.classify_target(
            _inventory("kosa_agent", extra_tables=("document_corpus",))
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"extensions": ("plpgsql",)},
        {"drop_tables": ("document",)},
        {"drop_tables": ("document_chunk",)},
    ],
)
def test_missing_rag_is_refused(kwargs: dict[str, Any]) -> None:
    with pytest.raises(transition.TransitionError) as caught:
        transition.classify_target(_inventory(**kwargs))
    assert caught.value.reason_code in {
        "RAG_PRESERVATION_FAILED",
        "TARGET_STATE_UNSUPPORTED",
    }


def test_empty_rag_tables_are_refused() -> None:
    """extension과 table이 있어도 비어 있으면 보존할 것이 없다."""

    inventory = _inventory()
    counts = dict(inventory.row_counts)
    counts["document"] = 0
    empty = transition.TargetInventory(**{**inventory.__dict__, "row_counts": counts})
    with pytest.raises(transition.TransitionError) as caught:
        transition.check_rag_presence(empty)
    assert caught.value.reason_code == "RAG_PRESERVATION_FAILED"


def test_cascade_foreign_key_is_refused() -> None:
    """`ON DELETE CASCADE`면 base 9 DELETE가 보존 table로 번진다."""

    fks = tuple(
        (name, child, cc, parent, pc, "c", update)
        for name, child, cc, parent, pc, _delete, update in (
            transition.EXPECTED_EXTERNAL_FKS_BY_PROFILE["runtime"]
        )
    )
    with pytest.raises(transition.TransitionError) as caught:
        transition.classify_target(_inventory(fks=fks))
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


def test_populated_foreign_key_child_is_refused() -> None:
    """child에 한 행이라도 있으면 자동 삭제하지 않고 멈춘다."""

    with pytest.raises(transition.TransitionError) as caught:
        transition.classify_target(_inventory(fk_child_rows={"approval_request": 1}))
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"view_sha": "9" * 64},
        {"view_owner": "someone_else"},
        {"view_acl": "{kosa=arwdDxt/kosa}"},
        {"view_comment": "임의 주석"},
    ],
)
def test_legacy_view_identity_drift_is_refused(kwargs: dict[str, Any]) -> None:
    with pytest.raises(transition.TransitionError) as caught:
        transition.classify_target(_inventory(**kwargs))
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


def test_unsupported_server_major_is_precondition() -> None:
    with pytest.raises(transition.TransitionError) as caught:
        transition.classify_target(_inventory(server_major=17))
    assert caught.value.reason_code == "BACKUP_CLIENT_UNAVAILABLE"
    assert caught.value.exit_code == transition.EXIT_CONFIRM_REQUIRED


# ---------------------------------------------------------------------------
# 호환 View 생성기
# ---------------------------------------------------------------------------

_LEGACY_BODY = (
    "SELECT 'TRACE' AS source, a.wafer AS wafer_no, h.lot_hist_id "
    "FROM trace_alarm_history a LEFT JOIN lot_history h ON h.wafer_no = a.wafer "
    "UNION ALL "
    "SELECT 'SUMMARY' AS source, a.wafer AS wafer_no, h.lot_hist_id "
    "FROM summary_alarm_history a LEFT JOIN lot_history h ON h.wafer_no = a.wafer;"
)


def test_compatibility_view_applies_both_substitutions_twice() -> None:
    sql = transition.build_compatibility_view_sql(_LEGACY_BODY)
    assert sql.count("h.wafer_no AS wafer_no") == 2
    assert sql.count("h.wafer_id = a.wafer") == 2
    assert "a.wafer AS wafer_no" not in sql
    assert "h.wafer_no = a.wafer" not in sql
    assert sql.startswith(f"CREATE VIEW public.{transition.LEGACY_VIEW} AS ")
    assert not sql.rstrip().endswith(";")


@pytest.mark.parametrize(
    "body",
    [
        _LEGACY_BODY.replace("a.wafer AS wafer_no", "x.wafer AS wafer_no", 1),
        _LEGACY_BODY.replace("h.wafer_no = a.wafer", "h.wafer_id = a.wafer", 1),
        _LEGACY_BODY + _LEGACY_BODY,
        "SELECT 1;",
    ],
)
def test_compatibility_view_refuses_unexpected_substitution_count(body: str) -> None:
    """치환 횟수가 각각 정확히 2가 아니면 만들지 않는다.

    정의가 달라졌다는 뜻이고, 그대로 만들면 consumer가 보는 shape가 바뀐다.
    """

    with pytest.raises(transition.TransitionError) as caught:
        transition.build_compatibility_view_sql(body)
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


def test_compatibility_view_r03_branch_is_untouched() -> None:
    body = _LEGACY_BODY.replace(
        ";", " UNION ALL SELECT 'R03' AS source, a.trigger_wafer_no AS wafer_no;"
    )
    sql = transition.build_compatibility_view_sql(body)
    assert "a.trigger_wafer_no AS wafer_no" in sql


def _mutate_fk(index: int, **changes: Any) -> tuple[transition.ForeignKeyTuple, ...]:
    fks = list(transition.EXPECTED_EXTERNAL_FKS_BY_PROFILE["runtime"])
    name, child, cc, parent, pc, delete, update = fks[index]
    fks[index] = (
        changes.get("name", name),
        changes.get("child", child),
        changes.get("child_columns", cc),
        changes.get("parent", parent),
        changes.get("parent_columns", pc),
        changes.get("delete", delete),
        changes.get("update", update),
    )
    return tuple(fks)


@pytest.mark.parametrize(
    "fks",
    [
        _mutate_fk(0, name="renamed_fkey"),
        _mutate_fk(0, child_columns=("id",)),
        _mutate_fk(0, parent_columns=("id",)),
        _mutate_fk(3, child_columns=("parameter_id",)),
        _mutate_fk(0, update="c"),
        _mutate_fk(0, delete="c"),
    ],
)
def test_foreign_key_identity_drift_is_refused(
    fks: tuple[transition.ForeignKeyTuple, ...],
) -> None:
    """이름·컬럼·update/delete action 중 무엇이 달라져도 거부한다.

    table pair만 비교하면 constraint rename과 column swap을 놓친다
    (구현리뷰 1차 필수 4).
    """

    with pytest.raises(transition.TransitionError) as caught:
        transition.classify_target(_inventory(fks=fks))
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


def test_external_fk_projection_is_deterministic_and_order_free() -> None:
    inventory = _inventory()
    reordered = transition.TargetInventory(
        **{
            **inventory.__dict__,
            "external_fks": tuple(reversed(inventory.external_fks)),
        }
    )
    assert transition.external_fk_projection_sha256(
        inventory
    ) == transition.external_fk_projection_sha256(reordered)
    drifted = transition.TargetInventory(
        **{**inventory.__dict__, "external_fks": _mutate_fk(0, name="renamed_fkey")}
    )
    assert transition.external_fk_projection_sha256(
        drifted
    ) != transition.external_fk_projection_sha256(inventory)


def test_runtime_and_evaluation_fk_projections_differ() -> None:
    assert transition.external_fk_projection_sha256(
        _inventory("kosa_agent")
    ) != transition.external_fk_projection_sha256(_inventory("kosa_text2sql"))


# ---------------------------------------------------------------------------
# 보존 projection 폭 — 구현리뷰 1차 필수 5
# ---------------------------------------------------------------------------


def _projection_differs(**kwargs: Any) -> bool:
    return transition.preserved_projection_sha256(
        _inventory(**kwargs)
    ) != transition.preserved_projection_sha256(_inventory())


def test_preserved_projection_catches_row_count_drift() -> None:
    assert _projection_differs(fk_child_rows={"audit_log": 1})


def test_preserved_projection_catches_index_drift() -> None:
    """행 수만 담으면 index가 사라져도 통과한다."""

    base = _inventory()
    stripped = {k: dict(v) for k, v in base.indexes.items()}
    stripped.pop("agent_run", None)
    assert _projection_differs(indexes=stripped)


def test_preserved_projection_catches_constraint_drift() -> None:
    base = _inventory()
    changed = {k: tuple(v) for k, v in base.constraints.items()}
    changed["audit_log"] = (("audit_log_pkey", "u"),)
    assert _projection_differs(constraints=changed)


def test_preserved_projection_catches_rag_fingerprint_drift() -> None:
    """RAG는 행 수가 같아도 내용이 바뀔 수 있다. B의 산식을 함께 본다."""

    assert _projection_differs(rag_live="9" * 64)
    assert _projection_differs(rag_embedding="9" * 64)


def test_preserved_projection_catches_missing_rag_extension() -> None:
    assert _projection_differs(extensions=("plpgsql",))


def test_handoff_index_is_named_in_the_projection() -> None:
    """`ux_document_corpus_active`를 projection이 **이름으로** 담아야 한다.

    상수만 선언하고 쓰지 않으면 index가 다른 table로 옮겨가도 알 수 없다.
    `document_corpus` 위의 index는 table catalog로도 잡히므로, 여기서는 handoff
    entry가 그 이름을 실제로 들고 있는지를 구조로 확인한다(필수 5).
    """

    projection = transition.preserved_projection(_inventory("kosa_text2sql"))
    handoff = projection[transition.LEGACY_HANDOFF_LABEL]
    assert set(handoff["indexes"]) == set(
        transition.LEGACY_HANDOFF_INDEXES_BY_TARGET["kosa_text2sql"]
    )
    assert handoff["indexes"]["ux_document_corpus_active"] is not None
    assert set(handoff["tables"]) == {"document", "document_chunk", "document_corpus"}

    # runtime target에는 handoff가 없다.
    runtime = transition.preserved_projection(_inventory("kosa_agent"))
    assert runtime[transition.LEGACY_HANDOFF_LABEL]["indexes"] == {}


def test_handoff_index_drift_changes_the_projection() -> None:
    base = _inventory("kosa_text2sql")
    stripped = {k: dict(v) for k, v in base.indexes.items()}
    stripped["document_corpus"] = {}
    assert transition.preserved_projection_sha256(
        _inventory("kosa_text2sql", indexes=stripped)
    ) != transition.preserved_projection_sha256(base)


def test_preserved_projection_does_not_require_content_hash() -> None:
    """일반 보존 table의 **값**은 재직렬화하지 않는다(계획 §4.2 유지)."""

    projection = transition.preserved_projection(_inventory())
    rendered = transition._canonical(projection)
    assert "content_hash" not in rendered
    assert set(projection) >= {
        "preserved",
        "preserved_sequences",
        "rag",
        "rag_live_fingerprint",
        "rag_embedding_projection",
        transition.LEGACY_HANDOFF_LABEL,
    }


def test_rag_live_fingerprint_reuses_b_formula() -> None:
    """2.6이 별도 산식을 만들면 기존 marker와 대조할 수 없다."""

    import load_rag_documents

    assert callable(load_rag_documents.live_fingerprint)
    source = transition._rag_live_fingerprint.__doc__ or ""
    assert "live_fingerprint" in transition.RAG_DOCUMENT_IDS_SQL or "doc_id" in (
        transition.RAG_DOCUMENT_IDS_SQL
    )
    assert "B-1.3" in source or "재사용" in source


# ---------------------------------------------------------------------------
# 구현리뷰 2차 필수 1-3 · 2 · 3
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"is_superuser": False},
        {"is_superuser": None},
        {"owner_match": False},
        {"owner_match": None},
    ],
)
def test_execution_privilege_is_measured_not_declared(kwargs: dict[str, Any]) -> None:
    """approval의 자기 선언이 아니라 live DB에서 읽은 값으로 판정한다."""

    with pytest.raises(transition.TransitionError) as caught:
        transition.classify_target(_inventory(**kwargs))
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


def test_base_catalog_hash_distinguishes_legacy_and_final() -> None:
    assert transition.LEGACY_BASE_CATALOG_SHA256 != transition.FINAL_BASE_CATALOG_SHA256


def test_non_wafer_base_column_drift_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """비-wafer column type이 달라져도 허용 상태로 오판하면 안 된다(필수 2)."""

    monkeypatch.setattr(transition, "base_catalog_sha256", lambda _i: "9" * 64)
    with pytest.raises(transition.TransitionError) as caught:
        transition.classify_target(_inventory())
    assert caught.value.reason_code == "TARGET_STATE_UNSUPPORTED"


def test_inventory_has_no_test_only_override_field() -> None:
    """production dataclass에 검사 우회 통로를 두지 않는다(구현리뷰 3차 권장 1)."""

    import dataclasses

    names = {f.name for f in dataclasses.fields(transition.TargetInventory)}
    assert not any("override" in name for name in names)
    assert not any("test" in name for name in names)


@pytest.mark.parametrize(
    "table",
    ["dim_parameter", "fdc_trace", "lot_history", "metrology", "summary_data"],
)
def test_any_base_table_row_drift_is_refused(table: str) -> None:
    """세 table만 보면 나머지 6개 drift를 놓친다(필수 2)."""

    inventory = _inventory()
    counts = dict(inventory.row_counts)
    counts[table] = counts[table] + 1
    drifted = transition.TargetInventory(**{**inventory.__dict__, "row_counts": counts})
    assert transition.classify_base(drifted) is transition.BaseState.PARTIAL_OR_DRIFT


def test_base_row_expectation_covers_all_nine_tables() -> None:
    for profile in ("runtime", "evaluation"):
        assert set(transition.LEGACY_BASE_ROWS[profile]) == set(transition.BASE_TABLES)
        assert set(transition.final_base_rows(profile)) == set(transition.BASE_TABLES)
    assert transition.LEGACY_BASE_ROWS["evaluation"]["action_history"] == 48
    assert transition.final_base_rows("evaluation")["action_history"] == 12
    assert transition.final_base_rows("runtime")["trace_alarm_history"] == 138


def test_preserved_catalog_includes_column_attributes_and_constraint_defs() -> None:
    """이름·kind만 담으면 CHECK 식이나 NOT NULL 변경을 놓친다(필수 3)."""

    detailed = {
        "audit_log": {
            "audit_id": {
                "data_type": "integer",
                "is_nullable": "NO",
                "column_default": "nextval('audit_log_audit_id_seq')",
            }
        }
    }
    base = _inventory(column_details=detailed)
    relaxed = {
        "audit_log": {
            "audit_id": {
                "data_type": "integer",
                "is_nullable": "YES",
                "column_default": "nextval('audit_log_audit_id_seq')",
            }
        }
    }
    assert transition.preserved_projection_sha256(
        _inventory(column_details=relaxed)
    ) != transition.preserved_projection_sha256(base)

    same_name_new_check = {k: tuple(v) for k, v in base.constraints.items()}
    same_name_new_check["audit_log"] = (("audit_log_pkey", "c", "CHECK (id > 0)"),)
    assert transition.preserved_projection_sha256(
        _inventory(column_details=detailed, constraints=same_name_new_check)
    ) != transition.preserved_projection_sha256(base)


# ---------------------------------------------------------------------------
# 구현리뷰 3차 필수 1 — typed content fingerprint
# ---------------------------------------------------------------------------


def test_same_row_count_value_drift_changes_target_fingerprint() -> None:
    """PK를 유지한 채 값 하나만 바꿔도 잡혀야 한다.

    catalog와 행 수만 보면 통과한다(구현리뷰 3차 필수 1).
    """

    base = _inventory()
    drifted = dict(base.base_content)
    drifted["lot_history"] = "9" * 64
    assert transition.target_fingerprint(
        _inventory(base_content=drifted)
    ) != transition.target_fingerprint(base)


def test_target_fingerprint_covers_base_preserved_view_and_fks() -> None:
    """preserved만 비교하면 base·View·FK 동시 변경을 놓친다."""

    base = _inventory()
    variants = {
        "base_content": {**dict(base.base_content), "metrology": "9" * 64},
        "view": None,
    }
    drifted_content = _inventory(base_content=variants["base_content"])
    drifted_view = _inventory(view_sha="9" * 64)
    drifted_acl = _inventory(view_acl="{kosa=arwdDxt/kosa}")
    drifted_fk = _inventory(fks=_mutate_fk(0, name="renamed_fkey"))
    reference = transition.target_fingerprint(base)
    for other in (drifted_content, drifted_view, drifted_acl, drifted_fk):
        assert transition.target_fingerprint(other) != reference


def test_final_state_requires_manifest_typed_content() -> None:
    """행 수·catalog가 최종과 같아도 값이 다르면 `FINAL_ADOPTED`가 아니다."""

    wrong = dict.fromkeys(transition.BASE_TABLES, "9" * 64)
    state = transition.classify_base(
        _inventory(
            wafer_type=transition.FINAL_WAFER_TYPE,
            alarms=FINAL_ALARMS,
            base_content=wrong,
        )
    )
    assert state is transition.BaseState.PARTIAL_OR_DRIFT


def test_missing_base_content_is_refused() -> None:
    """content hash를 계산하지 못한 상태를 승인에 동결하면 안 된다."""

    for content in (
        {},
        {"lot_history": "b" * 64},
        dict.fromkeys(transition.BASE_TABLES, "zz"),
    ):
        assert (
            transition.classify_base(_inventory(base_content=content))
            is transition.BaseState.PARTIAL_OR_DRIFT
        )


def test_final_base_content_uses_manifest_and_profile_projection() -> None:
    runtime = transition.final_base_content("runtime")
    evaluation = transition.final_base_content("evaluation")
    assert set(runtime) == set(transition.BASE_TABLES)
    assert runtime["action_history"] != evaluation["action_history"]
    for name in transition.BASE_TABLES:
        if name != "action_history":
            assert runtime[name] == evaluation[name]


# ---------------------------------------------------------------------------
# 구현리뷰 4차 필수 2 — 수집은 한 snapshot에서만
# ---------------------------------------------------------------------------


class _IsolationConnection:
    def __init__(self, level: str, *, in_transaction: bool = True) -> None:
        self.level = level
        self.in_transaction = in_transaction
        self.queries: list[str] = []

    def execute(self, statement: Any, parameters: Any = None) -> Any:
        sql = " ".join(str(statement).split())
        self.queries.append(sql)
        if sql.startswith("SELECT pg_current_xact_id_if_assigned()"):
            rows: list[dict[str, Any]] = [
                {
                    "assigned": self.in_transaction,
                    "started": self.in_transaction,
                    "pid": 1,
                }
            ]
        else:
            rows = [{"transaction_isolation": self.level}]

        class _Result:
            def mappings(self) -> Any:
                return self

            def all(self) -> Any:
                return rows

        return _Result()


@pytest.mark.parametrize("level", ["repeatable read", "serializable", "SERIALIZABLE"])
def test_snapshot_isolation_accepts_repeatable_read_and_stricter(level: str) -> None:
    assert transition.require_snapshot_isolation(_IsolationConnection(level)) == level


@pytest.mark.parametrize("level", ["read committed", "read uncommitted", ""])
def test_snapshot_isolation_rejects_read_committed(level: str) -> None:
    """READ COMMITTED이면 query마다 snapshot이 새로 잡혀 서로 다른 시점이 섞인다."""

    with pytest.raises(transition.TransitionError) as caught:
        transition.require_snapshot_isolation(_IsolationConnection(level))
    assert caught.value.reason_code == "SNAPSHOT_NOT_ISOLATED"


def test_read_inventory_checks_isolation_before_any_collection() -> None:
    """확인이 수집 뒤에 오면 이미 섞인 값을 만든 다음이다."""

    connection = _IsolationConnection("read committed")
    with pytest.raises(transition.TransitionError) as caught:
        transition.read_inventory(
            connection,
            database="kosa_agent",
            profile="runtime",
            require_snapshot=True,
        )
    assert caught.value.reason_code == "SNAPSHOT_NOT_ISOLATED"
    # isolation 확인 1건 외에는 아무 query도 보내지 않았다.
    assert connection.queries == [transition.ISOLATION_SQL]


def test_open_transaction_check_is_a_separate_step() -> None:
    """isolation 확인은 `SHOW`(snapshot 없음), transaction 확인은 `SELECT`다.

    둘을 한 함수에 묶으면 lock 전에 부를 수 없다 — SELECT가 snapshot을 고정하기 때문이다
    (구현리뷰 6차 필수 2).
    """

    connection = _IsolationConnection("repeatable read", in_transaction=False)
    # snapshot을 만들지 않는 확인은 통과한다.
    assert transition.require_snapshot_isolation(connection) == "repeatable read"
    assert connection.queries == [transition.ISOLATION_SQL]

    with pytest.raises(transition.TransitionError) as caught:
        transition.require_open_transaction(connection)
    assert caught.value.reason_code == "SNAPSHOT_NOT_ISOLATED"
    assert connection.queries[-1] == " ".join(transition.IN_TRANSACTION_SQL.split())


# ---------------------------------------------------------------------------
# 구현리뷰 13차 작업 중 발견 — 복원 비교는 column shape만 본다
# ---------------------------------------------------------------------------


def test_restore_comparison_ignores_constraints() -> None:
    """base 9만 dump하면 범위 밖 table을 참조하는 FK는 복원될 수 없다.

    전체 catalog hash를 비교하면 **정상 backup도** 영원히 `RESTORE_NOT_VERIFIED`가
    된다. column shape만 비교하고, 행 수·content는 따로 본다.
    """

    import dataclasses

    base = transition.BASE_TABLES[0]
    # 원본 base table에 **범위 밖 table을 참조하는 FK**가 있다.
    source = _inventory(
        "kosa_agent",
        constraints={base: ((f"{base}_r03_fkey", "f"), (f"{base}_pkey", "p"))},
    )
    # 복원본은 같은 column을 갖되 그 FK가 없다 — 부분 dump에서는 복원될 수 없다.
    restored = dataclasses.replace(source, constraints={base: ((f"{base}_pkey", "p"),)})
    # 전제: 전체 catalog projection은 다르다.
    assert transition.base_catalog_projection(source) != (
        transition.base_catalog_projection(restored)
    )
    assert transition.base_column_shape_sha256(
        restored
    ) == transition.base_column_shape_sha256(source)

    # 반대로 column이 달라지면 잡는다.
    drifted = _final("kosa_agent")
    assert transition.base_column_shape_sha256(
        drifted
    ) != transition.base_column_shape_sha256(source)


# ---------------------------------------------------------------------------
# 공용 preflight 실패 — kosa_text2sql의 구 epoch RAG (2026-08-22)
# ---------------------------------------------------------------------------


def test_b_managed_rag_targets_match_the_b_owned_allowlist() -> None:
    """2.6의 목록이 B의 목록과 갈리면 한쪽은 반드시 틀린다.

    B는 `apply_rag_schema.py`·`load_rag_documents.py`에서 `kosa_agent`와
    `kosa_agent_e2e`만 허용한다. 2.6이 세 DB 전부에 B의 fingerprint 산식을 돌려
    공용 preflight가 `UndefinedColumn`으로 죽었다.
    """

    import apply_rag_schema
    import load_rag_documents

    assert transition.B_MANAGED_RAG_TARGETS == apply_rag_schema.ALLOWED_RAG_DATABASES
    assert transition.B_MANAGED_RAG_TARGETS == load_rag_documents.ALLOWED_RAG_DATABASES
    # 전환 대상 셋 중 하나는 B 관리 밖이다. 그 하나가 이번 사고의 원인이다.
    assert set(transition.ORDERED_TARGETS) - transition.B_MANAGED_RAG_TARGETS == {
        "kosa_text2sql"
    }


#: 실제 호출 여부는 real PostgreSQL이 있는 격리 E2E에서 본다
#: (`test_transition_e2e_container.py`의 `..._rag_fingerprint_is_scoped_...`).
#: 여기서 fake connection을 만들면 `read_inventory()`의 query 20여 개를 흉내 내야 하고,
#: 그 흉내가 틀리면 회귀가 계약이 아니라 fake를 검증하게 된다.


@pytest.mark.parametrize(
    ("database", "b_managed"),
    [
        ("kosa_agent_e2e", True),
        ("kosa_agent", True),
        ("kosa_text2sql", False),
    ],
)
def test_rag_row_requirement_applies_only_to_b_managed_targets(
    database: str, b_managed: bool
) -> None:
    """행 수·전체 존재 요구는 **B가 적재한 target에만** 적용된다.

    `kosa_text2sql`의 같은 이름 table은 구 epoch(PR #48) 형상이고 행이 0이다. 거기에
    "행 > 0"을 요구하면 전환 자체가 시작되지 않는다.

    **이것은 "없어도 된다"가 아니다.** `expected_relations()`가 그 두 table을 legacy
    handoff에 포함하므로 full `check_relation_set()`은 여전히 **존재를 요구한다**. B가
    `V5-B-1.1`로 같은 이름의 새 schema를 교체하기 전까지 legacy 3종은 그대로 있어야 한다
    (구현리뷰 21차 편집 1).
    """

    intact = _inventory(database)
    transition.check_rag_presence(intact)

    empty = _inventory(database, rag_rows=0)
    if b_managed:
        with pytest.raises(transition.TransitionError) as caught:
            transition.check_rag_presence(empty)
        assert caught.value.reason_code == "RAG_PRESERVATION_FAILED"
    else:
        transition.check_rag_presence(empty)

    # `vector` extension은 세 DB 모두 필수다.
    with pytest.raises(transition.TransitionError) as caught:
        transition.check_rag_presence(_inventory(database, extensions=("plpgsql",)))
    assert caught.value.reason_code == "RAG_PRESERVATION_FAILED"


def test_relation_set_still_requires_the_legacy_rag_tables_on_text2sql() -> None:
    """행 요구를 뺐다고 **존재까지** 허용한 것은 아니다.

    두 계약이 갈리면 "없어도 통과"로 오해해 B가 지운 뒤 drift를 못 잡는다.
    """

    assert set(transition.RAG_TABLES) <= set(
        transition.LEGACY_HANDOFF_TABLES_BY_TARGET["kosa_text2sql"]
    )
    tables, _sequences = transition.expected_relations(_inventory("kosa_text2sql"))
    assert set(transition.RAG_TABLES) <= tables

    with pytest.raises(transition.TransitionError) as caught:
        transition.check_relation_set(
            _inventory("kosa_text2sql", drop_tables=transition.RAG_TABLES)
        )
    assert caught.value.reason_code in {
        "TARGET_STATE_UNSUPPORTED",
        "RAG_PRESERVATION_FAILED",
    }
