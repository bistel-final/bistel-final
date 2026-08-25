"""감사 read model 순수 로직 검증 — alias mapping · WHERE 조립 (V5-D-1.1·1.2)."""

from datetime import date

from app.analytics.audit import AUDIT_EVENT_TYPES, _build_where, _event_alias


def test_event_alias_approval_decided_boundary():
    assert _event_alias("APPROVAL_DECIDED", {"status": "APPROVED"}) == "APPROVE"
    assert _event_alias("APPROVAL_DECIDED", {"status": "REJECTED"}) == "REJECT"
    # 판정 불가면 canonical 유지
    assert _event_alias("APPROVAL_DECIDED", None) == "APPROVAL_DECIDED"
    assert _event_alias("APPROVAL_DECIDED", {"status": "PENDING"}) == "APPROVAL_DECIDED"


def test_event_alias_action_sent_channels():
    assert _event_alias("ACTION_SENT", {"channel": "EMAIL"}) == "NOTIFY"
    assert _event_alias("ACTION_SENT", {"channel": "MES_MOCK"}) == "SEND"
    assert _event_alias("ACTION_SENT", {"send_channel": "MES"}) == "SEND"
    assert _event_alias("ACTION_SENT", {}) == "ACTION_SENT"


def test_event_alias_hypothesis_and_passthrough():
    assert _event_alias("HYPOTHESIS_GENERATED", None) == "ACTION_RECOMMEND"
    assert _event_alias("DETECTION_COMPLETED", None) == "DETECTION_COMPLETED"
    assert _event_alias("AGENT_RUN_FAILED", {"x": 1}) == "AGENT_RUN_FAILED"


def test_build_where_no_filters_is_empty():
    where, params = _build_where(
        event_type=None,
        actor_type=None,
        entity_type=None,
        entity_id=None,
        date_from=None,
        date_to=None,
    )
    assert where == ""
    assert params == {}


def test_build_where_binds_all_filters_and_kst_boundary():
    where, params = _build_where(
        event_type="ACTION_SENT",
        actor_type="AGENT",
        entity_type="ACTION",
        entity_id="ACT-",
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 4),
    )
    # 값은 전부 bound parameter 로만 전달된다
    assert "ACT-" not in where
    assert params["entity_id"] == "%ACT-%"
    # NFR-13: Asia/Seoul 자정 경계 — 종료일 포함(다음날 자정 미만)
    assert params["date_from"].isoformat() == "2026-06-01T00:00:00+09:00"
    assert params["date_to"].isoformat() == "2026-06-05T00:00:00+09:00"
    assert "occurred_at >= :date_from" in where
    assert "occurred_at < :date_to" in where


def test_event_types_are_nine_canonical():
    assert len(AUDIT_EVENT_TYPES) == 9
    assert AUDIT_EVENT_TYPES[0] == "DETECTION_COMPLETED"
