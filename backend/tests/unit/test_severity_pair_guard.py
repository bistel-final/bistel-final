"""`V5-CM-3.3` action/severity pair guard 계약 회귀.

WBS 완료 기준은 "명명 CHECK로 반쪽 NULL 행을 차단한다"이다. 그 차단이 **아직
동작하지 않는다**는 것이 이 Task의 출발점이므로, 진리표를 코드가 아니라 계약에서
세운다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_agent_runtime as agent_runtime  # noqa: E402
import apply_severity_pair_guard as guard  # noqa: E402
import manifest_v3 as mv3  # noqa: E402


class TestTruthTable:
    """16조합은 **계약**이지 구현이 아니다."""

    def test_the_table_is_exhaustive(self) -> None:
        table = guard.truth_table()
        assert len(table) == 16
        assert len({(a, s) for a, s, _ in table}) == 16

    def test_exactly_four_are_accepted(self) -> None:
        assert guard.expected_counts() == (4, 12)

    def test_the_accepted_pairs_match_the_design(self) -> None:
        """설계서 §3.4 표 그대로다."""

        assert guard.ACCEPTED_PAIRS == {
            (None, None),
            ("MONITORING", "LOW"),
            ("WARNING", "MEDIUM"),
            ("EQP_HOLD", "HIGH"),
        }

    @pytest.mark.parametrize(
        ("action", "severity"),
        [
            (None, "LOW"),
            (None, "MEDIUM"),
            (None, "HIGH"),
            ("MONITORING", None),
            ("WARNING", None),
            ("EQP_HOLD", None),
        ],
    )
    def test_half_null_is_rejected(
        self, action: str | None, severity: str | None
    ) -> None:
        """**이 여섯 조합이 이 Task의 이유다.**

        익명 CHECK는 이것들을 통과시킨다 — 식 전체가 `NULL`이 되고 PostgreSQL은
        `FALSE`일 때만 거부한다. PostgreSQL 16 실측에서 16조합 중 10건이 수락됐다.
        """

        assert (action, severity) not in guard.ACCEPTED_PAIRS

    def test_mismatched_pairs_are_rejected(self) -> None:
        wrong = [
            (a, s)
            for a, s, ok in guard.truth_table()
            if not ok and a is not None and s is not None
        ]
        assert len(wrong) == 6


class TestGuardDefinition:
    """새 CHECK는 3값 논리에 기대지 않는다."""

    def test_it_never_evaluates_to_null(self) -> None:
        """`IS NOT NULL` 가드가 앞에 있으므로 어떤 입력에서도 boolean이다.

        SQL 3값 논리를 그대로 모사해 `NULL`로 빠지는 조합이 0인지 센다.
        """

        def eq(left: object, right: object) -> bool | None:
            return None if left is None or right is None else left == right

        def and_(*values: bool | None) -> bool | None:
            if any(v is False for v in values):
                return False
            return None if any(v is None for v in values) else True

        def or_(*values: bool | None) -> bool | None:
            if any(v is True for v in values):
                return True
            return None if any(v is None for v in values) else False

        results = []
        for action, severity, _ok in guard.truth_table():
            results.append(
                or_(
                    and_(action is None, severity is None),
                    and_(
                        action is not None,
                        severity is not None,
                        or_(
                            and_(eq(action, "MONITORING"), eq(severity, "LOW")),
                            and_(eq(action, "WARNING"), eq(severity, "MEDIUM")),
                            and_(eq(action, "EQP_HOLD"), eq(severity, "HIGH")),
                        ),
                    ),
                )
            )
        assert sum(1 for r in results if r is True) == 4
        assert sum(1 for r in results if r is False) == 12
        assert sum(1 for r in results if r is None) == 0, "NULL로 빠지는 조합이 있다"

    def test_the_predecessor_does_evaluate_to_null(self) -> None:
        """**반대 방향.** 구 CHECK가 실제로 구멍이라는 사실을 고정한다.

        이것이 없으면 "왜 successor가 필요한가"를 아무도 지키지 않는다.
        """

        predecessor = agent_runtime.EXPECTED_CONSTRAINTS["agent_run_check1"].definition
        assert "is not null" not in predecessor
        assert predecessor.startswith("check (action is null and severity is null or")

    def test_the_definition_matches_the_measured_catalog(self) -> None:
        """PostgreSQL 16 실측 정규화값이다 — 손으로 적은 기대값이 아니다."""

        assert guard.GUARD_DEFINITION.startswith("check (")
        assert "action is not null" in guard.GUARD_DEFINITION
        assert "severity is not null" in guard.GUARD_DEFINITION
        for token in ("monitoring", "low", "warning", "medium", "eqp_hold", "high"):
            assert token in guard.GUARD_DEFINITION


class TestMigrationSql:
    """003은 predecessor를 원자 교체한다."""

    def test_it_is_exactly_two_statements(self) -> None:
        _sql, statements = guard.load_and_validate_sql()
        assert len(statements) == 2
        assert statements[0].upper().startswith("DO ")
        assert statements[1].upper().startswith("ALTER TABLE")

    def test_it_drops_and_adds_in_one_statement(self) -> None:
        """두 문장으로 나누면 사이에 창이 생긴다."""

        _sql, statements = guard.load_and_validate_sql()
        alter = statements[1].lower()
        assert "drop constraint agent_run_check1" in alter
        assert "add constraint ck_agent_run_action_severity_pair" in alter

    def test_not_valid_is_forbidden(self) -> None:
        """`NOT VALID`을 쓰면 기존 row가 재검증을 건너뛴다.

        이 migration이 막으려는 바로 그 구멍이 남는다.
        """

        _sql, statements = guard.load_and_validate_sql()
        # **주석이 아니라 실행 문장을 본다.** 003 docstring은 `NOT VALID`을 쓰지
        # 않는 이유를 설명하므로 원문 전체를 훑으면 그 설명에 걸린다.
        for statement in statements:
            body = agent_runtime._strip_comments(statement)
            assert not re.search(r"\bNOT\s+VALID\b", body, re.I)

    @pytest.mark.parametrize(
        ("label", "body"),
        [
            (
                "DML",
                "ALTER TABLE t DROP CONSTRAINT agent_run_check1, ADD CONSTRAINT "
                "ck_agent_run_action_severity_pair CHECK (true); "
                "INSERT INTO t VALUES (1)",
            ),
            (
                "NOT VALID",
                "ALTER TABLE t DROP CONSTRAINT agent_run_check1, ADD CONSTRAINT "
                "ck_agent_run_action_severity_pair CHECK (true) NOT VALID",
            ),
            (
                "IF EXISTS",
                "ALTER TABLE t DROP CONSTRAINT IF EXISTS agent_run_check1, "
                "ADD CONSTRAINT ck_agent_run_action_severity_pair CHECK (true)",
            ),
        ],
    )
    def test_a_relaxed_statement_is_refused(
        self, tmp_path: Path, label: str, body: str
    ) -> None:
        path = tmp_path / "bad.sql"
        path.write_text(f"DO $$ BEGIN NULL; END $$;\n{body};\n", encoding="utf-8")
        with pytest.raises(guard.SeverityGuardError):
            guard.load_and_validate_sql(path)


class TestSuccessorStage:
    """successor는 predecessor를 **깨지 않고 더한다**."""

    def test_the_stage_is_registered(self) -> None:
        assert ("runtime", "runtime_guarded") in mv3.BOOTSTRAP_STAGE_CONTRACTS

    def test_the_predecessor_stage_survives(self) -> None:
        """CM-3.2 marker가 `runtime_clean`을 증명하고 있다.

        registry에서 빼면 그 marker가 검증할 계약을 잃는다.
        """

        assert ("runtime", "runtime_clean") in mv3.BOOTSTRAP_STAGE_CONTRACTS

    def test_the_lineage_appends_003(self) -> None:
        clean = mv3.BOOTSTRAP_STAGE_CONTRACTS[("runtime", "runtime_clean")]
        guarded = mv3.BOOTSTRAP_STAGE_CONTRACTS[("runtime", "runtime_guarded")]
        assert guarded.applied_migrations == (
            *clean.applied_migrations,
            "003_agent_run_severity_pair",
        )
        assert guard.EXPECTED_LINEAGE == guarded.applied_migrations

    def test_the_manifest_matches_the_predecessor_except_stage(self) -> None:
        """table inventory·hash는 같다 — 바뀐 것은 stage와 lineage뿐이다."""

        import json

        root = Path(mv3.MANIFEST_REGISTRY_ROOT)
        clean = json.loads(
            (root / "runtime.runtime_clean.json").read_text(encoding="utf-8")
        )
        guarded = json.loads(
            (root / "runtime.runtime_guarded.json").read_text(encoding="utf-8")
        )
        assert guarded["tables"] == clean["tables"]
        assert guarded["dataset_epoch"] == clean["dataset_epoch"]
        assert guarded["source_archive_sha256"] == clean["source_archive_sha256"]

        differing = {k for k in guarded if guarded[k] != clean.get(k)}
        assert differing == {"bootstrap_stage", "schema_stage", "applied_migrations"}


class TestStateMachine:
    """판정을 runner 본문에서 뗀 이유는 **판정 자체를 회귀로 잡기 위해서**다."""

    def _inspection(self, state: str) -> guard.GuardInspection:
        return guard.GuardInspection(state, None, None, "a" * 64)

    def test_baseline_is_appliable(self) -> None:
        assert (
            guard.classify_state(self._inspection("BASELINE_MARKED"), None)
            == "BASELINE_MARKED"
        )

    def test_guarded_with_marker_is_a_no_op(self) -> None:
        assert (
            guard.classify_state(self._inspection("GUARDED_UNMARKED"), {"x": 1})
            == "GUARDED_MARKED"
        )

    def test_guarded_without_receipt_is_unproven(self) -> None:
        """receipt 없는 수동 DDL을 조용히 축복하지 않는다."""

        assert (
            guard.classify_state(self._inspection("GUARDED_UNMARKED"), None)
            == "UNPROVEN_GUARDED"
        )

    def test_guarded_with_receipt_is_recoverable(self) -> None:
        assert (
            guard.classify_state(
                self._inspection("GUARDED_UNMARKED"),
                None,
                receipt={"status": "COMMITTED"},
            )
            == "GUARDED_UNMARKED"
        )

    @pytest.mark.parametrize("state", ["PARTIAL_OR_DRIFT", "DRIFT"])
    def test_broken_states_pass_through(self, state: str) -> None:
        assert guard.classify_state(self._inspection(state), None) == state

    def test_every_result_is_declared(self) -> None:
        seen = {
            guard.classify_state(self._inspection(s), m, receipt=r)
            for s in (
                "BASELINE_MARKED",
                "GUARDED_UNMARKED",
                "PARTIAL_OR_DRIFT",
                "DRIFT",
            )
            for m in (None, {"x": 1})
            for r in (None, {"status": "COMMITTED"})
        }
        assert seen <= guard.GUARD_STATES

    def test_every_producer_of_a_state_is_declared(self) -> None:
        """상태를 내는 **모든 생산자**를 본다.

        `classify_state()`만 보면 부족하다. `run_preflight()`는 그것을 우회해
        `INVALID_EXISTING_ROWS`·`GUARDED_DRIFT`·`BASELINE_DRIFT`를 직접 낸다.
        실제로 `BASELINE_DRIFT`가 선언 없이 새어 나갔다 — `GUARDED_DRIFT`는
        필수 J에서 선언까지 했는데 대칭이 깨져 있었다.
        """

        import ast
        import inspect

        for fn in (guard.run_preflight, guard.run_verify, guard.run_recover_marker):
            tree = ast.parse(inspect.getsource(fn).lstrip())
            returned = {
                node.value.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Return)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            }
            # `RECOVERED`는 상태가 아니라 복구 결과다.
            returned -= {"RECOVERED"}
            assert returned <= guard.GUARD_STATES, (fn.__name__, returned)

    def test_refuse_never_returns(self) -> None:
        import typing

        assert typing.get_type_hints(guard._refuse)["return"] is typing.NoReturn


class TestMatrixContract:
    def test_assert_matrix_accepts_the_canonical_result(self) -> None:
        accepted = tuple(sorted(guard.ACCEPTED_PAIRS, key=lambda p: str(p)))
        rejected = tuple((a, s) for a, s, ok in guard.truth_table() if not ok)
        guard.assert_matrix(
            guard.MatrixResult(accepted, rejected, (guard.GUARD_CONSTRAINT,))
        )

    def test_a_count_mismatch_is_refused(self) -> None:
        with pytest.raises(guard.SeverityGuardStateError, match="16조합"):
            guard.assert_matrix(guard.MatrixResult(((None, None),), (), ()))

    def test_right_count_wrong_pairs_is_refused(self) -> None:
        """개수만 맞고 조합이 다를 수 있다."""

        accepted = (
            (None, None),
            ("MONITORING", "HIGH"),
            ("WARNING", "LOW"),
            ("EQP_HOLD", "MEDIUM"),
        )
        rejected = tuple((a, s) for a, s, ok in guard.truth_table() if not ok)
        with pytest.raises(guard.SeverityGuardStateError, match="수락된 조합"):
            guard.assert_matrix(
                guard.MatrixResult(accepted, rejected, (guard.GUARD_CONSTRAINT,))
            )

    def test_a_foreign_constraint_name_is_refused(self) -> None:
        """다른 제약에 걸린 거부를 pair guard 증거로 오인하지 않는다."""

        accepted = tuple(guard.ACCEPTED_PAIRS)
        rejected = tuple((a, s) for a, s, ok in guard.truth_table() if not ok)
        with pytest.raises(guard.SeverityGuardStateError, match="constraint 이름"):
            guard.assert_matrix(
                guard.MatrixResult(accepted, rejected, ("agent_run_status_check",))
            )


# ---------------------------------------------------------------------------
# 구현리뷰 보완 회귀
# ---------------------------------------------------------------------------


class TestStageAwarePostcheck:
    """**필수 1.** `EXPECTED_STAGES`만 올리면 검증이 통째로 죽는다."""

    def test_both_runtime_stages_run_the_postcheck(self) -> None:
        import verify_bootstrap_state as verifier

        assert verifier.RUNTIME_POSTCHECK_STAGES == {
            ("runtime", "runtime_clean"),
            ("runtime", "runtime_guarded"),
        }

    def test_the_live_stage_is_covered(self) -> None:
        """live stage가 postcheck 집합 밖이면 그 DB는 검증이 0회다."""

        import verify_bootstrap_state as verifier

        for database, stage in verifier.EXPECTED_STAGES.items():
            if database == "kosa_text2sql":
                continue
            assert ("runtime", stage) in verifier.RUNTIME_POSTCHECK_STAGES, database

    def test_the_guarded_branch_is_wired(self) -> None:
        """`_guarded_mismatches()`가 실제로 호출되는지 본다."""

        import ast
        import inspect

        import verify_bootstrap_state as verifier

        source = inspect.getsource(verifier.verify_database)
        called = {
            node.func.id
            for node in ast.walk(ast.parse(source.lstrip()))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "_guarded_mismatches" in called


class TestSingleSourceOfTruth:
    """**필수 3.** 정본이 두 갈래면 양쪽 다 green이 된다."""

    def test_live_final_stage_agrees_across_modules(self) -> None:
        import apply_reference_extensions_v5 as v5
        import verify_bootstrap_state as verifier

        assert dict(v5.FINAL_STAGE_BY_PROFILE) == dict(
            verifier.LIVE_FINAL_STAGE_BY_PROFILE
        )

    def test_the_runtime_live_stage_is_guarded(self) -> None:
        import apply_reference_extensions_v5 as v5

        assert v5.FINAL_STAGE_BY_PROFILE["runtime"] == guard.GUARDED_STAGE
        assert v5.PROFILE_MIGRATIONS["runtime"] == guard.EXPECTED_LINEAGE

    def test_the_registrar_stage_is_separate(self) -> None:
        """CM-1.8 registrar는 자기가 발급한 stage를 본다.

        한 상수로 묶으면 registrar가 만들지 않은 stage의 manifest를 발급하려 한다.
        """

        import apply_reference_extensions_v5 as v5

        assert v5.REGISTRAR_STAGE_BY_PROFILE["runtime"] == "runtime_clean"
        assert (
            v5.REGISTRAR_STAGE_BY_PROFILE["runtime"]
            != v5.FINAL_STAGE_BY_PROFILE["runtime"]
        )

    def test_the_predecessor_still_routes_to_final_reference(self) -> None:
        import verify_bootstrap_state as verifier

        assert ("runtime", "runtime_clean") in verifier.FINAL_STAGES
        assert ("runtime", guard.GUARDED_STAGE) in verifier.FINAL_STAGES


class TestPredecessorDefinitionGuard:
    """**필수 4.** 이름만 보면 변조된 predecessor를 그대로 drop한다."""

    def test_the_expected_definition_is_the_measured_one(self) -> None:
        assert guard.PREDECESSOR_DEFINITION_SQL.startswith("CHECK (action IS NULL")
        assert "::text" in guard.PREDECESSOR_DEFINITION_SQL

    def test_the_sql_guard_compares_the_definition(self) -> None:
        sql, statements = guard.load_and_validate_sql()
        assert guard.PREDECESSOR_DEFINITION_SQL in statements[0]
        assert "pg_get_constraintdef" in statements[0]

    def test_a_guard_without_the_comparison_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "weak.sql"
        path.write_text(
            "DO $$ BEGIN\n"
            "  IF current_database() NOT IN ('kosa_agent') THEN\n"
            "    RAISE EXCEPTION 'x'; END IF;\n"
            "  PERFORM pg_get_constraintdef(1);\n"
            "END $$;\n"
            f"ALTER TABLE public.{guard.GUARD_TABLE} "
            f"DROP CONSTRAINT {guard.PREDECESSOR_CONSTRAINT}, "
            f"ADD CONSTRAINT {guard.GUARD_CONSTRAINT} CHECK (true);\n",
            encoding="utf-8",
        )
        with pytest.raises(guard.SeverityGuardError, match="predecessor 정의"):
            guard.load_and_validate_sql(path)

    def test_a_different_table_is_refused(self, tmp_path: Path) -> None:
        sql, statements = guard.load_and_validate_sql()
        path = tmp_path / "other.sql"
        path.write_text(
            statements[0]
            + ";\n"
            + statements[1].replace(f"public.{guard.GUARD_TABLE}", "public.audit_log")
            + ";\n",
            encoding="utf-8",
        )
        with pytest.raises(guard.SeverityGuardError, match="만 대상"):
            guard.load_and_validate_sql(path)


class TestCliBoundary:
    """**필수 2.** mutation을 암묵적 기본값으로 두지 않는다."""

    def test_a_missing_mode_is_refused(self) -> None:
        args = guard._parser().parse_args(["--database", "kosa_agent"])
        with pytest.raises(guard.SeverityGuardError, match="mode"):
            guard.resolve_mode(args)

    @pytest.mark.parametrize(
        "argv",
        [
            ["--preflight", "--apply"],
            ["--apply", "--verify"],
            ["--verify", "--verify-matrix"],
        ],
    )
    def test_modes_are_mutually_exclusive(self, argv: list[str]) -> None:
        args = guard._parser().parse_args(argv)
        with pytest.raises(Exception, match="하나만"):
            guard.resolve_mode(args)

    def test_evaluation_is_refused_before_credentials(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            guard, "load_dotenv", lambda *a, **k: calls.append("dotenv")
        )
        monkeypatch.setattr(
            guard, "load_bootstrap_target", lambda *a, **k: calls.append("loader")
        )

        assert guard.main(["--database", "kosa_text2sql", "--apply"]) == 3

        assert calls == [], "거부되는 입력에 자격증명을 읽으면 안 된다"
        assert "reason=PROFILE_NOT_ALLOWED" in capsys.readouterr().err

    def test_apply_requires_confirmation(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(guard, "load_dotenv", lambda *a, **k: None)
        monkeypatch.setattr(guard, "load_bootstrap_target", lambda db: _target(db))

        assert guard.main(["--database", "kosa_agent", "--apply"]) == 2
        assert "GUARD_FAIL" in capsys.readouterr().err


def _target(database: str = "kosa_agent") -> Any:
    import db_target

    return db_target.BootstrapTarget(
        host="db.invalid",
        port=5432,
        username="bootstrap",
        password="hidden",
        database=database,
        profile="runtime",
    )


class TestReceiptValueContract:
    """**필수 F.** key가 아니라 값이 계약이다."""

    def _committed(self) -> dict[str, Any]:
        identity = {
            "dataset_epoch": "fdc_final_20260818",
            "source_archive_sha256": "e" * 64,
            "bootstrap_stage": guard.GUARDED_STAGE,
            "manifest_sha256": "a" * 64,
            "predecessor_marker_sha256": "b" * 64,
            "predecessor_schema_signature_sha256": "c" * 64,
        }
        return {
            "artifact_type": guard.RECEIPT_ARTIFACT_TYPE,
            "format_version": 1,
            "task_id": guard.TASK_ID,
            "operation_id": "11111111-1111-1111-1111-111111111111",
            "database": "kosa_agent",
            "profile": "runtime",
            "status": "COMMITTED",
            "migration_id": guard.MIGRATION_ID,
            "migration_sha256": "d" * 64,
            "guarded_identity": identity,
            "change_reference": "GH-126",
            "started_at": "2026-08-24T00:00:00+00:00",
            "agent_run_rows_before": 0,
            "committed_at": "2026-08-24T00:00:00+00:00",
            "agent_run_rows_after": 0,
            "baseline_schema_signature_sha256": "c" * 64,
            "guarded_schema_signature_sha256": "f" * 64,
            "matrix_accepted": 4,
            "matrix_rejected": 12,
        }

    def test_the_canonical_receipt_is_accepted(self) -> None:
        guard.validate_receipt(self._committed(), database="kosa_agent")

    @pytest.mark.parametrize(
        ("label", "field", "value"),
        [
            ("format", "format_version", 999),
            ("uuid", "operation_id", "not-a-uuid"),
            ("naive 시각", "committed_at", "2026-08-24T00:00:00"),
            ("started naive", "started_at", "2026-08-24T00:00:00"),
            ("baseline 형식", "baseline_schema_signature_sha256", "bad"),
            ("guarded 형식", "guarded_schema_signature_sha256", "bad"),
            ("matrix accepted", "matrix_accepted", 0),
            ("matrix rejected", "matrix_rejected", 0),
            ("identity 빈 값", "guarded_identity", {}),
            ("행 수 불일치", "agent_run_rows_after", 1),
        ],
    )
    def test_a_single_mutation_is_refused(
        self, label: str, field: str, value: Any
    ) -> None:
        payload = self._committed()
        payload[field] = value
        with pytest.raises(guard.SeverityGuardArtifactError):
            guard.validate_receipt(payload, database="kosa_agent")

    def test_signatures_must_differ(self) -> None:
        """교체가 일어났으면 signature가 반드시 달라진다."""

        payload = self._committed()
        payload["guarded_schema_signature_sha256"] = payload[
            "baseline_schema_signature_sha256"
        ]
        with pytest.raises(guard.SeverityGuardArtifactError, match="signature가 같"):
            guard.validate_receipt(payload, database="kosa_agent")

    def test_baseline_must_match_identity(self) -> None:
        payload = self._committed()
        payload["baseline_schema_signature_sha256"] = "9" * 64
        with pytest.raises(guard.SeverityGuardArtifactError, match="baseline"):
            guard.validate_receipt(payload, database="kosa_agent")

    def test_recovery_consumes_the_receipt_matrix(self) -> None:
        """canonical 값을 새로 합성하지 않는다."""

        import inspect

        source = inspect.getsource(guard.run_recover_marker)
        assert "_matrix_from_receipt(receipt)" in source
        assert "tuple(ACCEPTED_PAIRS)" not in source

        matrix = guard._matrix_from_receipt(self._committed())
        assert matrix.counts == (4, 12)

        bad = self._committed()
        bad["matrix_accepted"] = 3
        with pytest.raises(guard.SeverityGuardArtifactError, match="matrix"):
            guard._matrix_from_receipt(bad)


class TestStageAwareMarkerComparison:
    """**필수 D.** guarded에서 predecessor signature는 live와 **달라야** 정상이다."""

    def test_the_shared_branch_skips_guarded(self) -> None:
        import inspect

        import verify_bootstrap_state as verifier

        source = inspect.getsource(verifier.verify_database)
        assert "stage != severity_guard.GUARDED_STAGE" in source

    def test_the_guarded_branch_links_predecessor_to_successor(self) -> None:
        import inspect

        import verify_bootstrap_state as verifier

        source = inspect.getsource(verifier._guarded_mismatches)
        assert "baseline_schema_signature_sha256" in source
        assert "predecessor" in source


def test_both_mutation_paths_share_one_precondition() -> None:
    """**필수 E·G.** 리허설이 실제보다 느슨하면 리허설을 통과한 것이 배포에서 막힌다.

    문자열이 아니라 **같은 helper를 부르는지**를 본다. 3차에서는 각 함수 안의 문구를
    찾았는데, helper로 뺀 순간 그 회귀가 무의미해졌다(구현리뷰 필수 I가 지적한 유형).
    """

    import ast
    import inspect

    for fn in (guard.run_apply, guard.run_rehearse):
        tree = ast.parse(inspect.getsource(fn).lstrip())
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "assert_baseline_precondition" in called, fn.__name__


def test_the_precondition_runs_before_any_write() -> None:
    """DDL·receipt 앞에 있어야 아무것도 남지 않는다."""

    import ast
    import inspect

    tree = ast.parse(inspect.getsource(guard.run_apply).lstrip())
    # 호출 이름과 **줄 번호**로 본다. 문자열로 찾으면 실행 helper를 바꿀 때마다
    # 회귀가 깨진다 — 실제로 `exec_driver_sql(statement)`가
    # `agent_runtime.execute_schema()`로 바뀌며 깨졌다.
    seen: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else None
        )
        if name in ("assert_baseline_precondition", "_start_receipt", "execute_schema"):
            seen.setdefault(name, node.lineno)
    assert set(seen) == {
        "assert_baseline_precondition",
        "_start_receipt",
        "execute_schema",
    }, seen
    gate = seen["assert_baseline_precondition"]
    assert seen["_start_receipt"] > gate
    assert seen["execute_schema"] > gate


class TestExactIntegerContract:
    """**필수 H.** `True == 1`·`4.0 == 4`이므로 동등 비교만으로는 타입이 눌린다."""

    @pytest.mark.parametrize(
        ("label", "value", "expected"),
        [
            ("정수", 4, True),
            ("bool True", True, False),
            ("bool False", False, False),
            ("실수", 4.0, False),
            ("문자열", "4", False),
            ("None", None, False),
        ],
    )
    def test_only_int_passes(self, label: str, value: Any, expected: bool) -> None:
        assert guard._is_exact_int(value) is expected

    def test_the_expected_value_is_checked(self) -> None:
        assert guard._is_exact_int(4, 4) is True
        assert guard._is_exact_int(3, 4) is False

    def test_marker_and_receipt_share_the_helper(self) -> None:
        """두 validator가 같은 helper를 쓴다 — 대칭이 깨지면 한쪽만 느슨해진다."""

        import inspect

        for fn in (guard.validate_marker, guard.validate_receipt):
            assert "_is_exact_int" in inspect.getsource(fn), fn.__name__


def test_the_baseline_precondition_runs_the_full_postcheck() -> None:
    """**필수 G.** signature 비교만으로는 `PUBLIC` privilege drift를 못 본다.

    실행 증명은 container가 한다(`test_a_public_grant_stops_the_apply`).
    여기서는 helper가 full postcheck를 부르는 것만 고정한다.
    """

    import inspect

    source = inspect.getsource(guard.assert_baseline_precondition)
    assert "postcheck_database" in source
    assert "EXPECTED_CONSTRAINTS" in source


class TestGuardedPostcondition:
    """**필수 J.** 적용 후에도 물리 계약 전체를 본다."""

    def test_it_uses_the_guarded_allowlist(self) -> None:
        """baseline이 아니라 guarded로 부른다 — 003이 이미 얹혀 있다."""

        import inspect

        source = inspect.getsource(guard.assert_guarded_postcondition)
        assert "postcheck_database" in source
        assert "GUARDED_CONSTRAINTS" in source

    def test_signature_has_exactly_one_computation(self) -> None:
        """**signature 정본이 하나인지 본다**(팀 리뷰 필수 2).

        `inspect_guard()`가 `_canonical_hash(_json_safe(signature))`를 따로 계산했다.
        marker는 그 값을, `assert_guarded_marker_agrees()`의 live 비교는
        `postcheck_database()` 값을 썼다. 두 값이 같았던 것은 `_json_safe()`가 이
        payload에서 항등이기 때문이며 **우연에 걸려 있었다.**

        `build_schema_signature()`가 `Decimal`·`datetime`·tuple을 하나라도 담게 되면
        apply는 성공하고 `--verify`가 곧바로 `DRIFT`로 떨어진다.

        이제 둘 다 `agent_runtime.schema_signature_sha256()`을 부른다.
        """

        import ast
        import inspect

        tree = ast.parse(inspect.getsource(guard.inspect_guard).lstrip())
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "schema_signature_sha256" in called
        # **코드만 본다.** 주석에는 왜 그렇게 했는지가 적혀 있어 문자열로 세면
        # 설명 문구에 걸린다.
        assert not (called & {"_canonical_hash", "_json_safe"})
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert not (names & {"_canonical_hash", "_json_safe"})

        # 두 경로가 한 계산으로 모인다 — `postcheck_database()`도 같은 helper를 탄다.
        runtime_src = inspect.getsource(
            guard.agent_runtime._validate_signature_contract
        )
        assert "_canonical_hash(signature)" in runtime_src

    def test_every_read_path_uses_it(self) -> None:
        """**DB를 읽는 모든 진입점이** 물리 계약을 본다.

        필수 G·J는 이 규칙의 서로 다른 칸이었다. G는 적용 전, J는 적용 후만
        닫았고, 그 뒤에도 `run_preflight()`의 baseline 분기와
        `run_recover_marker()`가 남아 있었다 — 지적된 축만 고치면 인접 경로에
        같은 원인이 그대로 산다.

        그래서 함수를 열거해 두고 **빠진 칸이 생기면 실패**하게 한다. 새 진입점을
        더하면 이 목록도 같이 고쳐야 한다.
        """

        import ast
        import inspect

        # 각 진입점이 반드시 불러야 하는 물리 계약. `run_verify_matrix`는
        # `run_verify()`를 경유하므로 그 위임을 대신 고정한다.
        required = {
            # marker가 없으면 물리 계약만, 있으면 marker까지 본다.
            "run_preflight": {
                "assert_baseline_precondition",
                "assert_guarded_postcondition",
                "assert_guarded_marker_agrees",
            },
            "run_rehearse": {"assert_baseline_precondition"},
            # no-op은 `GUARDED_MARKED`라 marker가 반드시 있다.
            "run_apply": {
                "assert_baseline_precondition",
                "assert_guarded_marker_agrees",
            },
            "run_verify": {"assert_guarded_marker_agrees"},
            "run_verify_matrix": {"run_verify"},
            # 복구는 marker를 **만드는** 중이라 대조할 marker가 없다.
            "run_recover_marker": {"assert_guarded_postcondition"},
        }

        # 자기 transaction을 여는 진입점이 아니라, 이미 열린 transaction 안에서
        # savepoint로 도는 helper다. 부르는 쪽(apply·rehearse·verify_matrix)이
        # 이미 계약을 확인했으므로 여기서 또 볼 것이 없다.
        helpers = {"run_matrix"}

        # 목록이 실제 진입점 전체를 덮는지 먼저 본다 — 진입점이 늘었는데 목록이
        # 그대로면 여기서 걸린다. 면제도 이름으로 적어야 통과한다.
        entrypoints = {
            name
            for name in dir(guard)
            if name.startswith("run_") and callable(getattr(guard, name))
        }
        assert entrypoints == set(required) | helpers, entrypoints ^ (
            set(required) | helpers
        )

        for name, expected in required.items():
            tree = ast.parse(inspect.getsource(getattr(guard, name)).lstrip())
            called = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            assert expected <= called, (name, expected - called)

    def test_preflight_covers_both_inspection_states(self) -> None:
        """`inspect_guard()`가 내는 두 상태 **모두** 물리 계약을 지난다.

        `if`만 두 번 쓰면 한쪽이 조용히 빠진다. `if/else`로 써서 분기가
        구조적으로 빠질 수 없게 한다.
        """

        import ast
        import inspect

        tree = ast.parse(inspect.getsource(guard.run_preflight).lstrip())
        branch = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.If) and "BASELINE_MARKED" in ast.unparse(node.test)
        )
        assert branch.orelse, "baseline 분기에 else가 없다"
        assert "assert_baseline_precondition" in ast.unparse(branch.body)
        assert "assert_guarded_postcondition" in ast.unparse(branch.orelse)

    def test_recovery_checks_the_contract_before_writing_the_marker(self) -> None:
        """복구도 marker를 쓰기 **전에** 본다 — 순서가 뒤집히면 의미가 없다."""

        import inspect

        source = inspect.getsource(guard.run_recover_marker)
        assert source.index("assert_guarded_postcondition(") < source.index(
            "save_marker("
        )

    def test_the_no_op_checks_before_returning(self) -> None:
        """확인 없는 no-op은 "아무 일도 없었다"가 아니라 "아무것도 보지 않았다"이다."""

        import inspect

        source = inspect.getsource(guard.run_apply)
        gate = source.index("assert_guarded_marker_agrees(")
        assert source.index('return "NO_OP"', gate) > gate

    def test_guarded_drift_is_a_declared_state(self) -> None:
        assert "GUARDED_DRIFT" in guard.GUARD_STATES
        with pytest.raises(guard.SeverityGuardStateError) as caught:
            guard._refuse("GUARDED_DRIFT")
        assert caught.value.reason_code == "GUARDED_DRIFT"


@pytest.mark.parametrize("value", [0.0, True, False, -1, "0", None])
def test_a_non_integer_after_row_is_refused(value: Any) -> None:
    """**필수 H-2.** `0.0 == 0`·`False == 0`이라 동등 비교로는 눌린다."""

    identity = {
        "dataset_epoch": "fdc_final_20260818",
        "source_archive_sha256": "e" * 64,
        "bootstrap_stage": guard.GUARDED_STAGE,
        "manifest_sha256": "a" * 64,
        "predecessor_marker_sha256": "b" * 64,
        "predecessor_schema_signature_sha256": "c" * 64,
    }
    payload = {
        "artifact_type": guard.RECEIPT_ARTIFACT_TYPE,
        "format_version": 1,
        "task_id": guard.TASK_ID,
        "operation_id": "11111111-1111-1111-1111-111111111111",
        "database": "kosa_agent",
        "profile": "runtime",
        "status": "COMMITTED",
        "migration_id": guard.MIGRATION_ID,
        "migration_sha256": "d" * 64,
        "guarded_identity": identity,
        "change_reference": "GH-126",
        "started_at": "2026-08-24T00:00:00+00:00",
        "agent_run_rows_before": 0,
        "committed_at": "2026-08-24T00:00:00+00:00",
        "agent_run_rows_after": value,
        "baseline_schema_signature_sha256": "c" * 64,
        "guarded_schema_signature_sha256": "f" * 64,
        "matrix_accepted": 4,
        "matrix_rejected": 12,
    }
    with pytest.raises(guard.SeverityGuardArtifactError):
        guard.validate_receipt(payload, database="kosa_agent")
