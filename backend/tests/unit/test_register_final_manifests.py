"""최종 profile manifest 발급 runner 계약 (`V5-CM-1.8` 묶음 2).

**공용 DB에 접근하지 않는다.** 계획 §5가 "묶음 2도 먼저 fake connection과 격리
fixture로 검증한다"고 정했고, 실제 read-only 대조는 묶음 2 리뷰 필수 0 이후 묶음 3
시작 시점으로 고정돼 있다.
"""

from __future__ import annotations

import contextlib
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import apply_reference_extensions_v5 as v5  # noqa: E402
import final_profile_manifests as candidates  # noqa: E402
import manifest_v3  # noqa: E402
import register_final_manifests as registrar  # noqa: E402
import verify_bootstrap_state as verifier  # noqa: E402

GOOD_RAG: dict[str, dict[str, Any]] = {
    "document": {"row_count": 3, "content_hash": "a" * 64},
    "document_chunk": {"row_count": 25, "content_hash": "b" * 64},
}


@pytest.fixture(scope="module")
def bundle() -> dict[str, dict[str, Any]]:
    return candidates.build_final_bundle(runtime_rag=GOOD_RAG)


class _Result:
    def __init__(self, exit_code: int, status: str = "PASS") -> None:
        self.exit_code = exit_code
        self.status = status


def _ok_verify(calls: list[dict[str, Any]]):
    def _verify(database: str, stage: str, **kwargs: Any) -> _Result:
        calls.append({"database": database, "stage": stage, **kwargs})
        return _Result(verifier.EXIT_OK)

    return _verify


@pytest.fixture(autouse=True)
def isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bundle: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """저장소를 건드리지 않고 같은 구조를 tmp에 만든다.

    **`autouse`다.** 처음에는 발급 테스트에만 걸었는데, `--confirm` gate를 제거하는
    변이(M54)를 돌리자 gate에 의존하던 lifecycle 테스트가 **실제 저장소 manifest를
    교체했다.** 코드가 올바를 때만 안전한 테스트는 격리된 것이 아니다.
    """

    active = tmp_path / "manifests"
    history = tmp_path / "history" / "manifests"
    active.mkdir(parents=True)

    paths = {
        profile: active / f"{profile}.{v5.FINAL_STAGE_BY_PROFILE[profile]}.json"
        for profile in v5.FINAL_STAGE_BY_PROFILE
    }
    retired = active / "evaluation.evaluation_mock.json"

    # 구 계보 상태를 재현한다.
    old_runtime = {"legacy": "runtime"}
    paths["runtime"].write_text(json.dumps(old_runtime), encoding="utf-8")
    retired.write_text(json.dumps({"legacy": "evaluation"}), encoding="utf-8")

    monkeypatch.setattr(registrar, "ACTIVE_PATHS", paths)
    monkeypatch.setattr(registrar, "RETIRED_ACTIVE_PATH", retired)
    monkeypatch.setattr(registrar, "HISTORY_ROOT", history)
    return {"paths": paths, "retired": retired, "history": history}


# ---------------------------------------------------------------------------
# 1. 고정 3 target — 임의 DB 우회 없음
# ---------------------------------------------------------------------------


class TestFixedBundleTargets:
    def test_the_three_targets_are_fixed_and_ordered(self) -> None:
        assert registrar.BUNDLE_TARGETS == (
            ("kosa_agent_e2e", "runtime"),
            ("kosa_agent", "runtime"),
            ("kosa_text2sql", "evaluation"),
        )

    def test_every_target_is_an_allowed_database(self) -> None:
        for database, profile in registrar.BUNDLE_TARGETS:
            assert database in verifier.ALLOWED_DATABASES
            assert verifier.DATABASE_PROFILE[database] == profile

    def test_the_targets_cover_the_declared_applies_to(self) -> None:
        """`PROFILE_APPLIES_TO`가 말하는 DB를 하나도 빠뜨리지 않는다."""

        declared = {
            (database, profile)
            for profile, databases in manifest_v3.PROFILE_APPLIES_TO.items()
            for database in databases
        }

        assert set(registrar.BUNDLE_TARGETS) == declared

    def test_the_cli_takes_no_positional_target(self) -> None:
        """임의 DB 하나만 발급하는 우회 경로를 만들지 않는다(계획 §3.6)."""

        parser = registrar._parser()
        positionals = [a for a in parser._actions if not a.option_strings]

        assert positionals == []
        flags = {a.option_strings[0] for a in parser._actions if a.option_strings}
        # `--read-public`은 공용 DB 조회 opt-in이며 target을 고르지 않는다.
        assert flags == {"-h", "--confirm", "--read-public"}

    def test_all_three_targets_are_verified(
        self, bundle: dict[str, dict[str, Any]]
    ) -> None:
        calls: list[dict[str, Any]] = []

        registrar.verify_bundle(bundle, verify=_ok_verify(calls))

        assert [c["database"] for c in calls] == [
            "kosa_agent_e2e",
            "kosa_agent",
            "kosa_text2sql",
        ]

    def test_the_same_runtime_candidate_goes_to_both_runtime_targets(
        self, bundle: dict[str, dict[str, Any]]
    ) -> None:
        """**두 Runtime DB에 같은 후보를 넘긴다.**

        DB별로 후보를 만들면 서로 다른 상태가 각자 "정답"이 된다(계획 §5 묶음 2-3).
        """

        calls: list[dict[str, Any]] = []
        registrar.verify_bundle(bundle, verify=_ok_verify(calls))

        runtime = [c["candidate"] for c in calls if c["database"] != "kosa_text2sql"]
        assert len(runtime) == 2
        assert runtime[0] is runtime[1]

    def test_each_target_gets_its_final_stage(
        self, bundle: dict[str, dict[str, Any]]
    ) -> None:
        calls: list[dict[str, Any]] = []
        registrar.verify_bundle(bundle, verify=_ok_verify(calls))

        for call in calls:
            profile = verifier.DATABASE_PROFILE[call["database"]]
            assert call["stage"] == v5.FINAL_STAGE_BY_PROFILE[profile]


# ---------------------------------------------------------------------------
# 2. 부분 실패 — 하나라도 실패하면 전체 실패
# ---------------------------------------------------------------------------


class TestPartialFailure:
    @pytest.mark.parametrize(
        "failing", ["kosa_agent_e2e", "kosa_agent", "kosa_text2sql"]
    )
    def test_any_failing_target_fails_the_whole_bundle(
        self, bundle: dict[str, dict[str, Any]], failing: str
    ) -> None:
        def _verify(database: str, stage: str, **kwargs: Any) -> _Result:
            if database == failing:
                return _Result(verifier.EXIT_MISMATCH, "FAIL")
            return _Result(verifier.EXIT_OK)

        with pytest.raises(registrar.RegistrarError):
            registrar.verify_bundle(bundle, verify=_verify)

    def test_a_failing_first_target_stops_before_the_rest(
        self, bundle: dict[str, dict[str, Any]]
    ) -> None:
        """**첫 실패에서 멈춘다.** 뒤 target을 계속 열 이유가 없다."""

        seen: list[str] = []

        def _verify(database: str, stage: str, **kwargs: Any) -> _Result:
            seen.append(database)
            return _Result(verifier.EXIT_MISMATCH, "FAIL")

        with pytest.raises(registrar.RegistrarError):
            registrar.verify_bundle(bundle, verify=_verify)

        assert seen == ["kosa_agent_e2e"]

    def test_a_narrowed_bundle_is_refused(
        self, bundle: dict[str, dict[str, Any]]
    ) -> None:
        partial = {"runtime": bundle["runtime"]}

        with pytest.raises(registrar.RegistrarError):
            registrar.verify_bundle(partial, verify=_ok_verify([]))

    def test_a_failing_verify_writes_nothing(
        self, bundle: dict[str, dict[str, Any]], tmp_path: Path
    ) -> None:
        with pytest.raises(registrar.RegistrarError):
            registrar.verify_bundle(
                bundle, verify=lambda *a, **k: _Result(verifier.EXIT_MISMATCH, "FAIL")
            )

        # 저장소 상태는 그대로다.
        assert registrar.RETIRED_ACTIVE_PATH.exists()


# ---------------------------------------------------------------------------
# 3. preview / confirm / no-op
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_without_confirm_it_previews_and_exits_three(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = registrar.run([], verify=_ok_verify([]), runtime_rag=GOOD_RAG)

        assert code == registrar.EXIT_CONFIRM_REQUIRED
        assert "--confirm" in capsys.readouterr().out

    def test_preview_does_not_touch_the_database(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """**preview는 DB를 열지 않는다.**"""

        calls: list[dict[str, Any]] = []
        registrar.run([], verify=_ok_verify(calls), runtime_rag=GOOD_RAG)

        assert calls == []

    def test_preview_leaks_no_values(self, bundle: dict[str, dict[str, Any]]) -> None:
        text = "\n".join(registrar.preview(bundle))

        for marker in ("postgresql://", "password", "/Users/", "@"):
            assert marker not in text, marker
        # row hash·경로 전체를 찍지 않는다.
        assert manifest_v3.FINAL_ARCHIVE_SHA256 not in text
        assert str(manifest_v3.MANIFEST_REGISTRY_ROOT) not in text

    def test_preview_names_every_bundle_path(
        self, bundle: dict[str, dict[str, Any]]
    ) -> None:
        lines = registrar.preview(bundle)

        assert any("runtime.runtime_clean" in line for line in lines)
        assert any("evaluation.evaluation_reference" in line for line in lines)
        assert any("evaluation.evaluation_mock" in line for line in lines)

    def test_the_state_is_pending_before_issuance(
        self, bundle: dict[str, dict[str, Any]]
    ) -> None:
        assert registrar.bundle_state(bundle) == "pending"


# ---------------------------------------------------------------------------
# 4. bundle 교체·rollback — 격리 경로에서만
# ---------------------------------------------------------------------------


class TestBundleCommit:
    def test_commit_replaces_both_and_removes_the_retired_one(
        self, isolated: dict[str, Any], bundle: dict[str, dict[str, Any]]
    ) -> None:
        registrar.commit_bundle(bundle)

        for profile, path in isolated["paths"].items():
            assert json.loads(path.read_text(encoding="utf-8")) == bundle[profile]
        assert not isolated["retired"].exists()

    def test_history_preserves_the_previous_bytes(
        self, isolated: dict[str, Any], bundle: dict[str, dict[str, Any]]
    ) -> None:
        registrar.commit_bundle(bundle)

        history = isolated["history"]
        assert json.loads(
            (history / "runtime.runtime_clean.json").read_text(encoding="utf-8")
        ) == {"legacy": "runtime"}
        assert json.loads(
            (history / "evaluation.evaluation_mock.json").read_text(encoding="utf-8")
        ) == {"legacy": "evaluation"}

    def test_a_conflicting_history_copy_stops_before_touching_active(
        self, isolated: dict[str, Any], bundle: dict[str, dict[str, Any]]
    ) -> None:
        """**두 계보가 같은 이름으로 겹치면 멈춘다.**"""

        history = isolated["history"]
        history.mkdir(parents=True)
        (history / "runtime.runtime_clean.json").write_text("{}", encoding="utf-8")

        with pytest.raises(registrar.RegistrarError):
            registrar.commit_bundle(bundle)

        assert json.loads(isolated["paths"]["runtime"].read_text(encoding="utf-8")) == {
            "legacy": "runtime"
        }
        assert isolated["retired"].exists()

    def test_a_failure_midway_rolls_every_path_back(
        self,
        isolated: dict[str, Any],
        bundle: dict[str, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """**두 번째 저장이 실패하면 첫 번째도 되돌린다**(계획 §3.6)."""

        before = {
            path: path.read_bytes() if path.exists() else None
            for path in (*isolated["paths"].values(), isolated["retired"])
        }

        calls = {"n": 0}
        real = manifest_v3.atomic_save_json

        def _flaky(path: Path, payload: Any) -> None:
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("디스크 오류")
            real(path, payload)

        monkeypatch.setattr(manifest_v3, "atomic_save_json", _flaky)

        with pytest.raises(OSError):
            registrar.commit_bundle(bundle)

        for path, payload in before.items():
            if payload is None:
                assert not path.exists(), path.name
            else:
                assert path.read_bytes() == payload, path.name

    def test_the_retired_active_is_removed_last(
        self,
        isolated: dict[str, Any],
        bundle: dict[str, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """새 파일이 저장되기 전에 구 evaluation을 지우면 안 된다.

        먼저 지우면 저장 실패 시 어느 evaluation manifest도 남지 않는다.
        """

        seen: list[bool] = []
        real = manifest_v3.atomic_save_json

        def _spy(path: Path, payload: Any) -> None:
            seen.append(isolated["retired"].exists())
            real(path, payload)

        monkeypatch.setattr(manifest_v3, "atomic_save_json", _spy)
        registrar.commit_bundle(bundle)

        assert all(seen), "구 active가 저장 도중에 이미 사라졌다"
        assert not isolated["retired"].exists()

    def _snapshot(self, isolated: dict[str, Any]) -> dict[Path, bytes | None]:
        paths = [*isolated["paths"].values(), isolated["retired"]]
        paths += [isolated["history"] / p.name for p in paths]
        return {p: (p.read_bytes() if p.exists() else None) for p in paths}

    def _assert_restored(self, before: dict[Path, bytes | None]) -> None:
        for path, payload in before.items():
            if payload is None:
                assert not path.exists(), path.name
            else:
                assert path.read_bytes() == payload, path.name

    def test_a_second_history_collision_leaves_nothing_written(
        self, isolated: dict[str, Any], bundle: dict[str, dict[str, Any]]
    ) -> None:
        """**첫 보존 성공 뒤 둘째 충돌**(구현리뷰 16차 필수 2).

        예전에는 첫 history 파일이 그대로 남아 "충돌이면 파일 쓰기 0"이 깨졌다.
        """

        history = isolated["history"]
        history.mkdir(parents=True)
        # evaluation 쪽만 충돌시킨다 — runtime은 먼저 보존에 성공할 수 있는 순서다.
        (history / isolated["retired"].name).write_text("{}", encoding="utf-8")
        before = self._snapshot(isolated)

        with pytest.raises(registrar.RegistrarError, match="history"):
            registrar.commit_bundle(bundle)

        self._assert_restored(before)

    def test_a_history_write_failure_rolls_history_back(
        self,
        isolated: dict[str, Any],
        bundle: dict[str, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """history write/readback 실패도 같은 경계 안이다."""

        before = self._snapshot(isolated)
        calls = {"n": 0}
        real = Path.write_bytes

        def _flaky(self: Path, payload: bytes) -> int:
            if isolated["history"] in self.parents:
                calls["n"] += 1
                if calls["n"] == 2:
                    raise OSError("디스크 오류")
            return real(self, payload)

        monkeypatch.setattr(Path, "write_bytes", _flaky)

        with pytest.raises(OSError):
            registrar.commit_bundle(bundle)

        monkeypatch.undo()
        self._assert_restored(before)

    def test_an_active_failure_rolls_history_back_too(
        self,
        isolated: dict[str, Any],
        bundle: dict[str, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """**active 저장 실패도 history를 되돌린다.**

        history를 rollback 밖에 두면 여기서 새 history 파일이 남는다.
        """

        before = self._snapshot(isolated)
        calls = {"n": 0}
        real = manifest_v3.atomic_save_json

        def _flaky(path: Path, payload: Any) -> None:
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("디스크 오류")
            real(path, payload)

        monkeypatch.setattr(manifest_v3, "atomic_save_json", _flaky)

        with pytest.raises(OSError):
            registrar.commit_bundle(bundle)

        self._assert_restored(before)

    def test_a_second_run_is_a_no_op(
        self,
        isolated: dict[str, Any],
        bundle: dict[str, dict[str, Any]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        registrar.commit_bundle(bundle)

        assert registrar.bundle_state(bundle) == "current"
        code = registrar.run(["--confirm"], verify=_ok_verify([]), runtime_rag=GOOD_RAG)
        assert code == registrar.EXIT_OK
        assert "이미" in capsys.readouterr().out

    def test_a_leftover_retired_manifest_keeps_the_state_pending(
        self, isolated: dict[str, Any], bundle: dict[str, dict[str, Any]]
    ) -> None:
        """**두 active가 맞아도 구 evaluation이 남아 있으면 끝난 게 아니다.**

        그 판정을 빼면 구 manifest가 active 디렉터리에 남은 채로 no-op이 된다.
        """

        for profile, path in isolated["paths"].items():
            path.write_text(json.dumps(bundle[profile]), encoding="utf-8")
        isolated["retired"].write_text("{}", encoding="utf-8")

        assert registrar.bundle_state(bundle) == "pending"

        isolated["retired"].unlink()
        assert registrar.bundle_state(bundle) == "current"

    def test_a_silently_corrupted_write_is_caught(
        self,
        isolated: dict[str, Any],
        bundle: dict[str, dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """**저장본 재독을 직접 겨냥한다.**

        정상 경로에서는 저장이 항상 성공하므로 재독이 발화하지 않는다. 저장이 다른
        내용을 쓰는 상황을 만들어야 그 방어가 검증된다.
        """

        def _corrupt(path: Path, payload: Any) -> None:
            path.write_text(json.dumps({"corrupted": True}), encoding="utf-8")

        monkeypatch.setattr(manifest_v3, "atomic_save_json", _corrupt)

        with pytest.raises(registrar.RegistrarError, match="재독"):
            registrar.commit_bundle(bundle)

        # rollback으로 원래 상태가 남는다.
        assert json.loads(isolated["paths"]["runtime"].read_text(encoding="utf-8")) == {
            "legacy": "runtime"
        }
        assert isolated["retired"].exists()

    def test_a_no_op_run_does_not_open_the_database(
        self, isolated: dict[str, Any], bundle: dict[str, dict[str, Any]]
    ) -> None:
        registrar.commit_bundle(bundle)

        calls: list[dict[str, Any]] = []
        registrar.run(["--confirm"], verify=_ok_verify(calls), runtime_rag=GOOD_RAG)

        assert calls == []

    def test_an_invalid_candidate_never_reaches_the_filesystem(
        self, isolated: dict[str, Any], bundle: dict[str, dict[str, Any]]
    ) -> None:
        broken = copy.deepcopy(bundle)
        broken["runtime"]["tables"]["action_history"]["row_count"] = 7

        with pytest.raises(manifest_v3.VerificationError):
            registrar.commit_bundle(broken)

        assert json.loads(isolated["paths"]["runtime"].read_text(encoding="utf-8")) == {
            "legacy": "runtime"
        }

    def test_confirm_verifies_before_writing(
        self, isolated: dict[str, Any], bundle: dict[str, dict[str, Any]]
    ) -> None:
        """**검증이 먼저다.** 실패하면 저장소는 그대로다."""

        with pytest.raises(registrar.RegistrarError):
            registrar.run(
                ["--confirm"],
                verify=lambda *a, **k: _Result(verifier.EXIT_MISMATCH, "FAIL"),
                runtime_rag=GOOD_RAG,
            )

        assert json.loads(isolated["paths"]["runtime"].read_text(encoding="utf-8")) == {
            "legacy": "runtime"
        }
        assert isolated["retired"].exists()


# ---------------------------------------------------------------------------
# 5. 후보 주입 경로 — 저장소를 읽지 않는다
# ---------------------------------------------------------------------------


class TestCandidateInjection:
    def test_verify_database_accepts_a_candidate(self) -> None:
        assert "candidate" in verifier.verify_database.__code__.co_varnames

    def test_the_candidate_path_does_not_read_the_registry(
        self, monkeypatch: pytest.MonkeyPatch, synthetic: dict[str, Any]
    ) -> None:
        """등록본이 아직 구 계보여도 후보만으로 대조할 수 있어야 한다."""

        def _boom(*_a: Any, **_k: Any) -> None:
            raise AssertionError("candidate 경로가 저장소 manifest를 읽었다")

        monkeypatch.setattr(manifest_v3, "resolve_bootstrap_manifest_path", _boom)

        calls: list[object] = []
        import db_target

        # target 해석에서 멈추지만, 그 전에 registry를 읽지 않는다는 것이 계약이다.
        with pytest.raises(db_target.TargetValidationError):
            verifier.verify_database(
                "kosa_text2sql",
                "evaluation_reference",
                environ={},
                engine_factory=lambda target: calls.append(target),
                candidate=synthetic,
            )
        assert calls == []


def test_no_test_in_this_file_can_touch_the_repository() -> None:
    """**격리 자체를 계약으로 잠근다.**

    `isolated`가 `autouse`이므로 module 전역이 tmp를 가리킨다. 이 단언이 깨지면
    어떤 테스트든 실제 manifest를 교체할 수 있는 상태다.
    """

    repository = manifest_v3.MANIFEST_REGISTRY_ROOT
    for path in (*registrar.ACTIVE_PATHS.values(), registrar.RETIRED_ACTIVE_PATH):
        assert repository not in path.parents, path
    assert repository not in registrar.HISTORY_ROOT.parents


# ---------------------------------------------------------------------------
# 6. candidate live acceptance — fake engine으로 끝까지 돌린다
# ---------------------------------------------------------------------------
#
# stub verifier만 쓰면 `verify_database(candidate=...)` 경로가 한 번도 실행되지 않는다.
# 계획 묶음 2가 요구한 transaction·identity·drift 계약이 회귀로 고정되지 않는다
# (구현리뷰 10차 필수 2).

ENV = {
    "POSTGRES_BOOTSTRAP_HOST": "shared.example",
    "POSTGRES_BOOTSTRAP_PORT": "5432",
    "POSTGRES_BOOTSTRAP_USER": "reader",
    "POSTGRES_BOOTSTRAP_PASSWORD": "hidden",
}


@pytest.fixture()
def env() -> dict[str, str]:
    from db_target import host_fingerprint

    return {
        **ENV,
        "POSTGRES_BOOTSTRAP_ALLOWED_HOST_SHA256": host_fingerprint(
            "shared.example", 5432
        ),
    }


class _CandidateConnection:
    """candidate와 **일치하는** DB를 흉내 낸다. drift는 인자로 준다."""

    def __init__(
        self,
        database: str,
        candidate: dict[str, Any],
        *,
        row_counts: dict[str, int] | None = None,
        column_types: dict[str, list[dict[str, str]]] | None = None,
        tables: set[str] | None = None,
    ) -> None:
        self.database = database
        self.candidate = candidate
        self.statements: list[str] = []
        self._row_counts = row_counts or {}
        self._column_types = column_types or {}
        self._tables = tables

    def _expected_rows(self, table: str) -> int:
        entry = self.candidate["tables"].get(table) or {}
        return int(entry.get("row_count") or 0)

    def begin(self) -> Any:
        return _NullContext()

    def __enter__(self) -> _CandidateConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def exec_driver_sql(self, statement: str, parameters: Any = None) -> Any:
        normalized = " ".join(statement.split())
        self.statements.append(normalized)

        if "current_database() AS database_name" in normalized:
            return _Rows(
                [
                    {
                        "database_name": self.database,
                        "public_exists": True,
                        "can_use": True,
                    }
                ]
            )
        if "FROM information_schema.tables" in normalized:
            names = self._tables
            if names is None:
                names = set(self.candidate["tables"])
            return _Rows([{"table_name": name} for name in sorted(names)])
        if normalized.startswith("SELECT has_table_privilege"):
            return _Rows(scalar=True)
        if "format_type(a.atttypid" in normalized and parameters:
            table = parameters[0]
            if table in self._column_types:
                return _Rows(self._column_types[table])
            types = verifier._expected_column_types(table)
            return _Rows(
                [
                    {"column_name": name, "data_type": _sql_type(logical)}
                    for name, logical in types.items()
                ]
            )
        # **catalog query를 먼저 본다.** generic `SELECT ... FROM`이 앞에 있으면
        # R03·View·comment query를 전부 삼킨다.
        if normalized.startswith("SELECT pg_get_viewdef"):
            return _Rows(scalar=_canonical_view_definition())
        if "obj_description" in normalized:
            return _Rows(
                [
                    {"relname": v5.R03_TABLE, "comment": v5.R03_COMMENT},
                    {"relname": v5.ALARM_VIEW, "comment": v5.VIEW_COMMENT},
                ]
            )
        if "pg_constraint" in normalized:
            return _Rows(_r03_constraint_rows())
        if "information_schema.columns" in normalized:
            if v5.R03_TABLE in normalized:
                return _Rows(_r03_column_rows())
            return _Rows(_view_column_rows())

        if normalized.startswith("SELECT count(*) FROM"):
            table = normalized.split('"')[1]
            return _Rows(scalar=self._row_counts.get(table, self._expected_rows(table)))
        if normalized.startswith("SELECT ") and ' FROM "' in normalized:
            table = normalized.split('FROM "')[-1].rstrip('"')
            entry = self.candidate["tables"].get(table) or {}
            count = self._row_counts.get(table, entry.get("row_count") or 0)
            # verifier가 `content_columns`로 좁혀 SELECT할 수 있다 — 요청한 컬럼만 준다.
            selected = [
                name.strip().strip('"')
                for name in normalized.split(" FROM ")[0]
                .removeprefix("SELECT ")
                .split(",")
            ]
            if count:
                return _Rows(
                    [_typed_row(table, selected, index) for index in range(count)]
                )
            return _Rows([])
        if normalized.startswith("SELECT"):
            return _Rows([])
        return _Rows()


class _NullContext:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        return None


class _Rows:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None):
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def scalar_one(self) -> Any:
        return self._scalar

    def scalar(self) -> Any:
        return self._scalar


class _Engine:
    def __init__(self, connection: _CandidateConnection) -> None:
        self.connection = connection
        self.disposed = False

    def connect(self) -> _CandidateConnection:
        return self.connection

    def dispose(self) -> None:
        self.disposed = True


def _sql_type(logical: str) -> str:
    return {
        "text": "character varying(24)",
        "numeric": "integer",
        "timestamp": "timestamp without time zone",
        "json": "jsonb",
        "boolean": "boolean",
        "vector": "vector(1024)",
    }.get(logical, "text")


def _r03_column_rows() -> list[dict[str, Any]]:
    return [
        {
            "column_name": name,
            "data_type": data_type,
            "character_maximum_length": length,
            "is_nullable": "YES" if nullable else "NO",
        }
        for name, data_type, length, nullable in v5.R03_COLUMNS
    ]


def _r03_constraint_rows() -> list[dict[str, Any]]:
    return [
        {"conname": name, "contype": contype, "definition": definition}
        for name, (contype, definition) in sorted(v5.R03_CONSTRAINT_DEFINITIONS.items())
    ]


def _view_column_rows() -> list[dict[str, Any]]:
    return [
        {"column_name": name, "data_type": data_type}
        for name, data_type in v5.VIEW_COLUMNS
    ]


#: 격리 PostgreSQL 16 실측 `pg_get_viewdef` 정규형. `V5-CM-3.1` fixture를 재사용한다.
CANONICAL_VIEW_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "v5_cm_3_1"
    / "canonical_view.sql"
)


def _canonical_view_definition() -> str:
    body = CANONICAL_VIEW_FIXTURE.read_text(encoding="utf-8")
    return "\n".join(
        line for line in body.splitlines() if not line.startswith("--")
    ).strip()


def _typed_row(table: str, columns: list[str], index: int) -> dict[str, Any]:
    """logical type에 맞는 값을 만든다.

    `normalize_db_row()`가 timestamp·numeric을 실제로 파싱하므로 아무 문자열이나
    넣으면 정규화 단계에서 깨진다.
    """

    types = verifier._expected_column_types(table)
    row: dict[str, Any] = {}
    for column in columns:
        logical = types.get(column, "text")
        if logical == "timestamp":
            row[column] = f"2026-08-{(index % 28) + 1:02d}T00:00:00"
        elif logical == "numeric":
            row[column] = index
        elif logical == "boolean":
            row[column] = index % 2 == 0
        elif logical == "json":
            row[column] = None
        else:
            row[column] = f"value-{index}"
    return row


def _module() -> Any:
    """이 test module 자신. monkeypatch 대상이다."""

    return sys.modules[__name__]


def _trim_to_empty(candidate: dict[str, Any], *, keep: set[str]) -> dict[str, Any]:
    """`keep` 외 immutable table을 0행으로 바꾼다.

    fake가 14,400행 base 데이터를 재현할 수 없으므로, 검사하려는 table만 남긴다.
    """

    tables: dict[str, Any] = {}
    for name, entry in candidate["tables"].items():
        if name in keep or entry["verification_policy"] != "immutable_content":
            tables[name] = entry
        else:
            tables[name] = {
                "columns": entry["columns"],
                "verification_policy": "bootstrap_empty",
                "row_count": 0,
                "content_hash": manifest_v3.hash_canonical_rows([]),
            }
    return {**candidate, "tables": tables}


def _synthetic_candidate() -> dict[str, Any]:
    """**통제 가능한 evaluation candidate.**

    실제 candidate는 `fdc_trace` 14,400행의 canonical hash를 담는다 — fake가 그 내용을
    재현할 수 없다. 이 테스트가 보려는 것은 transaction·identity·drift 축이지 base
    데이터 재현이 아니므로, 같은 stage 계약을 만족하는 최소 manifest를 쓴다.

    `action_history`만 stage 계약상 12행 immutable이라 행을 만들고 그 hash를 넣는다.
    """

    stage = v5.FINAL_STAGE_BY_PROFILE["evaluation"]
    contract = manifest_v3.BOOTSTRAP_STAGE_CONTRACTS[("evaluation", stage)]
    real = candidates.build_final_bundle(runtime_rag=GOOD_RAG)["evaluation"]

    tables: dict[str, Any] = {}
    for name, entry in real["tables"].items():
        columns = list(entry["columns"])
        if entry["verification_policy"] == "schema_only":
            tables[name] = {"columns": columns, "verification_policy": "schema_only"}
        elif name == "action_history":
            hashed = list(entry.get("content_columns") or columns)
            all_types = verifier._expected_column_types(name)
            types = {c: all_types[c] for c in hashed}
            rows = [
                verifier.normalize_db_row(_typed_row(name, hashed, index), types)
                for index in range(contract.action_rows)
            ]
            tables[name] = {
                "columns": columns,
                "verification_policy": "immutable_content",
                "row_count": contract.action_rows,
                "content_hash": manifest_v3.hash_canonical_rows(rows),
                "fixture_type": contract.action_fixture_type,
            }
        else:
            tables[name] = {
                "columns": columns,
                "verification_policy": "bootstrap_empty",
                "row_count": 0,
                "content_hash": manifest_v3.hash_canonical_rows([]),
            }

    return {**real, "tables": tables}


@pytest.fixture(scope="module")
def synthetic() -> dict[str, Any]:
    return _synthetic_candidate()


class TestCandidateLiveAcceptance:
    def test_a_matching_database_passes(
        self, synthetic: dict[str, Any], env: dict[str, str]
    ) -> None:
        connection = _CandidateConnection("kosa_text2sql", synthetic)
        result = verifier.verify_database(
            "kosa_text2sql",
            "evaluation_reference",
            environ=env,
            engine_factory=lambda _t: _Engine(connection),
            candidate=synthetic,
        )

        assert result.status == verifier.STATUS_PASS

    def test_the_first_statement_is_repeatable_read_and_read_only(
        self, synthetic: dict[str, Any], env: dict[str, str]
    ) -> None:
        """**계획 §1.2의 첫 문장 계약.**

        기존 SQL 순서 회귀는 `SKIP_KOSA_0813`이라 구 `SET TRANSACTION READ ONLY`를
        기대한 채로 남아 있었다 — 새 상수를 구 SQL로 되돌려도 실패하지 않았다.
        """

        connection = _CandidateConnection("kosa_text2sql", synthetic)
        verifier.verify_database(
            "kosa_text2sql",
            "evaluation_reference",
            environ=env,
            engine_factory=lambda _t: _Engine(connection),
            candidate=synthetic,
        )

        assert connection.statements[:3] == [
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
            "SET LOCAL search_path = public",
            "SET LOCAL statement_timeout = '30s'",
        ]

    def test_every_statement_is_read_only(
        self, synthetic: dict[str, Any], env: dict[str, str]
    ) -> None:
        connection = _CandidateConnection("kosa_text2sql", synthetic)
        verifier.verify_database(
            "kosa_text2sql",
            "evaluation_reference",
            environ=env,
            engine_factory=lambda _t: _Engine(connection),
            candidate=synthetic,
        )

        for statement in connection.statements:
            assert statement.upper().startswith(verifier.READ_ONLY_PREFIXES), statement

    def test_a_target_identity_mismatch_is_unverifiable(
        self, synthetic: dict[str, Any], env: dict[str, str]
    ) -> None:
        connection = _CandidateConnection("other_db", synthetic)

        with pytest.raises(verifier.UnverifiableError) as caught:
            verifier.verify_database(
                "kosa_text2sql",
                "evaluation_reference",
                environ=env,
                engine_factory=lambda _t: _Engine(connection),
                candidate=synthetic,
            )

        assert caught.value.reason_code == "TARGET_IDENTITY_MISMATCH"

    def test_the_old_inventory_is_refused(
        self, synthetic: dict[str, Any], env: dict[str, str]
    ) -> None:
        """구 14 table 형상은 최종 13과 다르다."""

        connection = _CandidateConnection(
            "kosa_text2sql",
            synthetic,
            tables=set(synthetic["tables"]) | {"document_corpus"},
        )

        with pytest.raises(verifier.AcceptanceMismatchError) as caught:
            verifier.verify_database(
                "kosa_text2sql",
                "evaluation_reference",
                environ=env,
                engine_factory=lambda _t: _Engine(connection),
                candidate=synthetic,
            )

        assert caught.value.details["mismatch_kind"] == "TABLE_INVENTORY"

    def test_a_row_count_drift_is_refused(
        self, synthetic: dict[str, Any], env: dict[str, str]
    ) -> None:
        connection = _CandidateConnection(
            "kosa_text2sql", synthetic, row_counts={"action_history": 48}
        )

        with pytest.raises(verifier.AcceptanceMismatchError) as caught:
            verifier.verify_database(
                "kosa_text2sql",
                "evaluation_reference",
                environ=env,
                engine_factory=lambda _t: _Engine(connection),
                candidate=synthetic,
            )

        kinds = {
            entry.get("mismatch_kind") for entry in caught.value.details["mismatches"]
        }
        assert "ROW_COUNT" in kinds

    def test_a_column_type_drift_is_refused(
        self, synthetic: dict[str, Any], env: dict[str, str]
    ) -> None:
        """`wafer`가 구 `smallint`면 거부한다(계획 §3.5-1)."""

        drifted = {
            "evaluation": [
                {
                    "column_name": name,
                    "data_type": "smallint" if name == "wafer" else _sql_type(logical),
                }
                for name, logical in verifier._expected_column_types(
                    "evaluation"
                ).items()
            ]
        }
        connection = _CandidateConnection(
            "kosa_text2sql", synthetic, column_types=drifted
        )

        with pytest.raises(verifier.AcceptanceMismatchError) as caught:
            verifier.verify_database(
                "kosa_text2sql",
                "evaluation_reference",
                environ=env,
                engine_factory=lambda _t: _Engine(connection),
                candidate=synthetic,
            )

        kinds = {
            entry.get("mismatch_kind") for entry in caught.value.details["mismatches"]
        }
        assert "COLUMN_TYPE" in kinds

    def test_a_same_row_count_content_mutation_is_refused(
        self, synthetic: dict[str, Any], env: dict[str, str]
    ) -> None:
        """**행 수는 같고 값만 바뀐 경우**(구현리뷰 11차 필수 2).

        `ROW_COUNT`가 아니라 `CONTENT_HASH`로 잡혀야 한다.
        """

        drifted = copy.deepcopy(synthetic)
        entry = drifted["tables"]["action_history"]
        entry["content_hash"] = "c" * 64

        connection = _CandidateConnection("kosa_text2sql", drifted)

        with pytest.raises(verifier.AcceptanceMismatchError) as caught:
            verifier.verify_database(
                "kosa_text2sql",
                "evaluation_reference",
                environ=env,
                engine_factory=lambda _t: _Engine(connection),
                candidate=drifted,
            )

        kinds = {e.get("mismatch_kind") for e in caught.value.details["mismatches"]}
        assert kinds == {"CONTENT_HASH"}

    @pytest.mark.parametrize("axis", ["definition", "columns"])
    def test_a_v4_view_is_refused(
        self,
        monkeypatch: pytest.MonkeyPatch,
        synthetic: dict[str, Any],
        env: dict[str, str],
        axis: str,
    ) -> None:
        """final View를 V4로 되돌리면 `FINAL_VIEW_CONTRACT`로 수렴한다."""

        if axis == "definition":
            monkeypatch.setattr(
                _module(), "_canonical_view_definition", lambda: "SELECT 1"
            )
        else:
            monkeypatch.setattr(
                _module(),
                "_view_column_rows",
                lambda: [{"column_name": "x", "data_type": "text"}],
            )

        connection = _CandidateConnection("kosa_text2sql", synthetic)

        with pytest.raises(verifier.AcceptanceMismatchError) as caught:
            verifier.verify_database(
                "kosa_text2sql",
                "evaluation_reference",
                environ=env,
                engine_factory=lambda _t: _Engine(connection),
                candidate=synthetic,
            )

        kinds = {e.get("mismatch_kind") for e in caught.value.details["mismatches"]}
        assert "FINAL_VIEW_CONTRACT" in kinds

    def test_a_runtime_candidate_passes_with_live_rag(
        self, markers: dict[str, Path], live: dict[str, Any], env: dict[str, str]
    ) -> None:
        """**Runtime 경로도 끝까지 돈다**(구현리뷰 11차 필수 2).

        production provenance 결과를 그대로 써서 RAG table hash 경계를 실제로 밟는다.
        """

        provenance = registrar.runtime_rag_provenance(
            _rag_readers(), columns=_rag_columns()
        )
        runtime = candidates.build_profile_candidate(
            "runtime",
            source=candidates.load_source_manifest(),
            runtime_rag=provenance,
        )
        trimmed = _trim_to_empty(runtime, keep={"document", "document_chunk"})

        connection = _CandidateConnection("kosa_agent", trimmed)
        with _healthy_runtime_postcheck():
            result = verifier.verify_database(
                "kosa_agent",
                "runtime_clean",
                environ=env,
                engine_factory=lambda _t: _Engine(connection),
                candidate=trimmed,
                require_runtime_marker=False,
            )

        assert result.status == verifier.STATUS_PASS
        assert connection.statements[0] == (
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        )
        assert trimmed["tables"]["document"]["row_count"] == 3
        assert trimmed["tables"]["document_chunk"]["row_count"] == 25

    def test_the_chunk_hash_is_not_the_marker_fingerprint(
        self, markers: dict[str, Path], live: dict[str, Any]
    ) -> None:
        """**combined fingerprint를 table hash로 재사용하지 않는다**(11차 필수 1).

        marker fingerprint는 documents·chunks·metadata를 묶은 loader 전용 값이라
        manifest의 table별 hash와 산식이 다르다. 그것을 넣으면 실제 DB가 marker와
        일치해도 `CONTENT_HASH`가 난다.
        """

        provenance = registrar.runtime_rag_provenance(
            _rag_readers(), columns=_rag_columns()
        )

        assert provenance["document"]["row_count"] == 3
        assert provenance["document_chunk"]["row_count"] == 25
        assert (
            provenance["document"]["content_hash"]
            != provenance["document_chunk"]["content_hash"]
        )
        assert (
            provenance["document_chunk"]["content_hash"]
            != live["live_db_fingerprint_sha256"]
        )

    def test_the_rag_hashes_survive_the_verifier(
        self, markers: dict[str, Path], live: dict[str, Any], env: dict[str, str]
    ) -> None:
        """**측정한 hash가 verifier 대조를 실제로 통과한다.**

        provenance와 verifier가 같은 column 순서·정규화를 쓰지 않으면 여기서 깨진다.
        """

        provenance = registrar.runtime_rag_provenance(
            _rag_readers(), columns=_rag_columns()
        )
        runtime = candidates.build_profile_candidate(
            "runtime",
            source=candidates.load_source_manifest(),
            runtime_rag=provenance,
        )
        trimmed = _trim_to_empty(runtime, keep={"document", "document_chunk"})

        import verify_bootstrap_state as v

        connection = _CandidateConnection("kosa_agent", trimmed)
        for table in ("document", "document_chunk"):
            entry = trimmed["tables"][table]
            # verifier와 같은 부분집합으로 읽는다.
            hashed = list(entry.get("content_columns") or entry["columns"])
            selected = ", ".join(f'"{c}"' for c in hashed)
            all_types = v._expected_column_types(table)
            types = {c: all_types[c] for c in hashed}
            rows = [
                v.normalize_db_row(row, types)
                for row in v._rows(
                    connection.exec_driver_sql(f'SELECT {selected} FROM "{table}"')
                )
            ]
            assert len(rows) == entry["row_count"], table
            assert manifest_v3.hash_canonical_rows(rows) == entry["content_hash"], table

    @pytest.mark.parametrize("rows", [0, 2, 4])
    def test_a_document_row_drift_is_caught_after_issuance(
        self,
        markers: dict[str, Path],
        live: dict[str, Any],
        env: dict[str, str],
        rows: int,
    ) -> None:
        """**발급 뒤 `document`가 바뀌면 잡힌다**(구현리뷰 16차 필수 1).

        `schema_only`로 뒀다면 3행이 0행이 돼도 통과했다.
        """

        trimmed = self._runtime_candidate()
        connection = _CandidateConnection(
            "kosa_agent", trimmed, row_counts={"document": rows}
        )

        with _healthy_runtime_postcheck():
            with pytest.raises(verifier.AcceptanceMismatchError) as caught:
                verifier.verify_database(
                    "kosa_agent",
                    "runtime_clean",
                    environ=env,
                    engine_factory=lambda _t: _Engine(connection),
                    candidate=trimmed,
                    require_runtime_marker=False,
                )

        kinds = {e.get("mismatch_kind") for e in caught.value.details["mismatches"]}
        assert kinds & {"ROW_COUNT", "CONTENT_HASH"}

    def test_a_document_content_drift_is_caught_after_issuance(
        self, markers: dict[str, Path], live: dict[str, Any], env: dict[str, str]
    ) -> None:
        """행 수는 같고 업무 컬럼만 바뀐 경우도 잡는다."""

        trimmed = self._runtime_candidate()
        trimmed["tables"]["document"]["content_hash"] = "d" * 64
        connection = _CandidateConnection("kosa_agent", trimmed)

        with _healthy_runtime_postcheck():
            with pytest.raises(verifier.AcceptanceMismatchError) as caught:
                verifier.verify_database(
                    "kosa_agent",
                    "runtime_clean",
                    environ=env,
                    engine_factory=lambda _t: _Engine(connection),
                    candidate=trimmed,
                    require_runtime_marker=False,
                )

        kinds = {e.get("mismatch_kind") for e in caught.value.details["mismatches"]}
        assert "CONTENT_HASH" in kinds

    def test_the_issued_manifest_pins_document_identity(self) -> None:
        """**발급된 실물이 3행과 검증 가능한 identity를 담는다.**"""

        issued = json.loads(
            (
                manifest_v3.MANIFEST_REGISTRY_ROOT / "runtime.runtime_clean.json"
            ).read_text(encoding="utf-8")
        )
        entry = issued["tables"]["document"]

        assert entry["verification_policy"] == "immutable_content"
        assert entry["row_count"] == 3
        assert manifest_v3.HEX_SHA256_PATTERN.fullmatch(entry["content_hash"])
        assert "created_at" in entry["columns"]
        assert "created_at" not in entry["content_columns"]

    def _runtime_candidate(self) -> dict[str, Any]:
        provenance = registrar.runtime_rag_provenance(
            _rag_readers(), columns=_rag_columns()
        )
        runtime = candidates.build_profile_candidate(
            "runtime",
            source=candidates.load_source_manifest(),
            runtime_rag=provenance,
        )
        return _trim_to_empty(runtime, keep={"document", "document_chunk"})

    def test_the_engine_is_disposed(
        self, synthetic: dict[str, Any], env: dict[str, str]
    ) -> None:
        engine = _Engine(_CandidateConnection("kosa_text2sql", synthetic))
        verifier.verify_database(
            "kosa_text2sql",
            "evaluation_reference",
            environ=env,
            engine_factory=lambda _t: engine,
            candidate=synthetic,
        )

        assert engine.disposed is True


# ---------------------------------------------------------------------------
# 7. production provenance·CLI 경계 — 주입 없이 실제 경로를 돈다
# ---------------------------------------------------------------------------
#
# 나머지 테스트가 `runtime_rag=GOOD_RAG`를 주입하므로 production Gate가 한 번도
# 실행되지 않는다. 구현이 있어도 회귀가 우회하면 없는 것과 같다(구현리뷰 10차 필수 1).


@pytest.fixture()
def markers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """저장소 marker를 tmp로 복사해 변조 가능하게 만든다."""

    import load_rag_documents as rag

    root = tmp_path / "markers"
    root.mkdir()
    paths: dict[str, Path] = {}
    for database in registrar.RUNTIME_DATABASES:
        source = rag.marker_path(database)
        target = root / source.name
        target.write_bytes(source.read_bytes())
        paths[database] = target

    monkeypatch.setattr(
        rag, "marker_path", lambda db, **_k: root / f"rag_load.{db}.json"
    )
    return paths


def _rewrite(path: Path, **changes: Any) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(changes)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _rag_readers(rows: dict[str, int] | None = None) -> dict[str, Any]:
    """두 Runtime DB의 read-only connection을 흉내 낸다.

    marker fingerprint를 그대로 돌려주도록 `rag.live_fingerprint`를 fake로 바꾸는 대신,
    실제 helper가 읽는 query에 결정론적 행을 준다.
    """

    return {database: _RagConnection(rows) for database in registrar.RUNTIME_DATABASES}


class _RagConnection:
    """`document`·`document_chunk` 조회에 고정 행을 돌려준다."""

    def __init__(
        self,
        rows: dict[str, int] | None = None,
        *,
        offset: int = 0,
        broken_embeddings: int = 0,
    ) -> None:
        self._rows = rows or dict(registrar.RUNTIME_RAG_ROWS)
        self._offset = offset
        self._broken_embeddings = broken_embeddings

    def exec_driver_sql(self, statement: str, parameters: Any = None) -> Any:
        normalized = " ".join(statement.split())
        if "vector_dims" in normalized:
            return _Rows(scalar=self._broken_embeddings)
        table = "document_chunk" if "document_chunk" in normalized else "document"
        count = self._rows.get(table, 0)
        # **측정이 SELECT한 컬럼만 돌려준다.** `created_at`처럼 hash에서 빠지는
        # 컬럼을 그대로 주면 `normalize_db_row`의 registry 대조가 깨진다.
        columns = [
            name.strip().strip('"')
            for name in normalized.split(" FROM ")[0].removeprefix("SELECT ").split(",")
        ]
        return _Rows(
            [_typed_row(table, list(columns), i + self._offset) for i in range(count)]
        )


def _rag_columns() -> dict[str, list[str]]:
    return {
        table: list(candidates._v4_columns(table))
        for table in registrar.RUNTIME_RAG_ROWS
    }


@pytest.fixture()
def live(monkeypatch: pytest.MonkeyPatch, markers: dict[str, Path]) -> dict[str, Any]:
    """`live_fingerprint()`가 marker 값을 재현하도록 고정한다.

    fake connection이 loader의 combined hash 산식을 재현할 수는 없다. 이 회귀가 보려는
    것은 **fingerprint를 대조한다는 사실**이지 그 산식이 아니다 — 산식 자체는
    `V5-B-1.3`이 소유한다.
    """

    import load_rag_documents as rag

    payload = json.loads(markers["kosa_agent"].read_text(encoding="utf-8"))
    monkeypatch.setattr(
        rag,
        "live_fingerprint",
        lambda _c, _ids: payload["live_db_fingerprint_sha256"],
    )
    monkeypatch.setattr(registrar.rag, "live_fingerprint", rag.live_fingerprint)
    return payload


class TestProductionProvenance:
    def test_the_repository_markers_pass_the_gate(
        self, markers: dict[str, Path], live: dict[str, Any]
    ) -> None:
        provenance = registrar.runtime_rag_provenance(
            _rag_readers(), columns=_rag_columns()
        )

        assert provenance["document"]["row_count"] == 3
        assert provenance["document_chunk"]["row_count"] == 25
        for entry in provenance.values():
            assert manifest_v3.HEX_SHA256_PATTERN.fullmatch(entry["content_hash"])

    def test_the_cli_builds_provenance_without_injection(
        self,
        monkeypatch: pytest.MonkeyPatch,
        markers: dict[str, Path],
        live: dict[str, Any],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """**주입 없이 CLI가 끝까지 간다.**

        production 경로가 없으면 `RAG_PROVENANCE_REQUIRED`로 preview조차 못 만든다.
        """

        monkeypatch.setattr(
            registrar,
            "_measure_runtime_rag_from_live",
            lambda **_k: registrar.runtime_rag_provenance(
                _rag_readers(), columns=_rag_columns()
            ),
        )

        code = registrar.run(["--read-public"], verify=_ok_verify([]))

        assert code == registrar.EXIT_CONFIRM_REQUIRED
        assert "변경 예정" in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("status", "PENDING"),
            ("profile", "evaluation"),
            ("document_count", 4),
            ("chunk_count", 24),
            ("null_embedding_count", 1),
            ("dimension", 768),
            ("artifact_type", "other"),
            ("database", "kosa_text2sql"),
        ],
    )
    @pytest.mark.parametrize("database", ["kosa_agent", "kosa_agent_e2e"])
    def test_a_marker_contract_violation_is_refused(
        self,
        markers: dict[str, Path],
        live: dict[str, Any],
        database: str,
        field: str,
        value: Any,
    ) -> None:
        _rewrite(markers[database], **{field: value})

        with pytest.raises(registrar.RegistrarError):
            registrar.runtime_rag_provenance(_rag_readers(), columns=_rag_columns())

    @pytest.mark.parametrize("field", registrar.RAG_CROSS_DATABASE_FIELDS)
    def test_two_runtime_markers_must_agree(
        self, markers: dict[str, Path], live: dict[str, Any], field: str
    ) -> None:
        """**두 DB가 같은 corpus를 담고 있어야** candidate 하나를 둘 다에 쓸 수 있다."""

        payload = json.loads(markers["kosa_agent"].read_text(encoding="utf-8"))
        drifted = payload[field]
        if isinstance(drifted, list):
            drifted = [*drifted, "extra"]
        elif isinstance(drifted, dict):
            drifted = {**drifted, "EXTRA": "0" * 64}
        else:
            drifted = f"{drifted}-drift"
        _rewrite(markers["kosa_agent"], **{field: drifted})

        # 정본 상수로 고정된 field는 **marker 계약 검사가 먼저** 잡는다. 그렇지 않은
        # field(fingerprint·corrected hash)만 상호 비교에 도달한다.
        expected = (
            "두 Runtime DB"
            if field not in registrar.RAG_MARKER_CONTRACT
            else "RAG marker"
        )
        with pytest.raises(registrar.RegistrarError, match=expected):
            registrar.runtime_rag_provenance(_rag_readers(), columns=_rag_columns())

    def test_a_corrupted_corrected_hash_is_refused(
        self, markers: dict[str, Path], live: dict[str, Any]
    ) -> None:
        for path in markers.values():
            payload = json.loads(path.read_text(encoding="utf-8"))
            corrected = dict(payload["corrected_sha256_by_document"])
            first = sorted(corrected)[0]
            corrected[first] = "not-a-hash"
            _rewrite(path, corrected_sha256_by_document=corrected)

        with pytest.raises(registrar.RegistrarError, match="corrected"):
            registrar.runtime_rag_provenance(_rag_readers(), columns=_rag_columns())

    def test_a_document_id_set_mismatch_is_refused(
        self, markers: dict[str, Path], live: dict[str, Any]
    ) -> None:
        for path in markers.values():
            payload = json.loads(path.read_text(encoding="utf-8"))
            _rewrite(path, document_ids=[*payload["document_ids"], "DOC-EXTRA"])

        # `document_ids`는 loader 정본 상수로 고정돼 있어 계약 검사가 먼저 잡는다.
        with pytest.raises(registrar.RegistrarError, match="RAG marker"):
            registrar.runtime_rag_provenance(_rag_readers(), columns=_rag_columns())

    def test_a_missing_marker_is_refused(
        self, markers: dict[str, Path], live: dict[str, Any]
    ) -> None:
        markers["kosa_agent"].unlink()

        with pytest.raises(registrar.RegistrarError):
            registrar.runtime_rag_provenance(_rag_readers(), columns=_rag_columns())

    def test_a_stale_marker_fingerprint_is_refused(
        self, markers: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**fresh fingerprint를 다시 잰다**(계획 §3.4 Gate 3).

        marker가 stale이면 그 자리에서 잡힌다.
        """

        import load_rag_documents as rag

        monkeypatch.setattr(rag, "live_fingerprint", lambda _c, _ids: "f" * 64)
        monkeypatch.setattr(registrar.rag, "live_fingerprint", rag.live_fingerprint)

        with pytest.raises(registrar.RegistrarError, match="fingerprint"):
            registrar.runtime_rag_provenance(_rag_readers(), columns=_rag_columns())

    def test_two_databases_with_different_content_are_refused(
        self, markers: dict[str, Path], live: dict[str, Any]
    ) -> None:
        """**두 DB 내용이 다르면 어느 쪽도 정답이 아니다**(계획 §3.4 Gate 4)."""

        readers = _rag_readers()
        readers["kosa_agent_e2e"] = _RagConnection(
            {"document": 3, "document_chunk": 25}
        )
        readers["kosa_agent"] = _RagConnection(
            {"document": 3, "document_chunk": 25}, offset=1
        )

        with pytest.raises(registrar.RegistrarError, match="RAG 내용"):
            registrar.runtime_rag_provenance(readers, columns=_rag_columns())

    @pytest.mark.parametrize("missing", ["kosa_agent", "kosa_agent_e2e"])
    def test_reading_only_one_database_is_refused(
        self, markers: dict[str, Path], live: dict[str, Any], missing: str
    ) -> None:
        """한 DB만 읽고 정답을 삼는 우회를 막는다."""

        readers = {db: conn for db, conn in _rag_readers().items() if db != missing}

        with pytest.raises(registrar.RegistrarError, match="두 곳을 모두"):
            registrar.runtime_rag_provenance(readers, columns=_rag_columns())

    @pytest.mark.parametrize(
        ("table", "count"), [("document", 2), ("document_chunk", 26)]
    )
    def test_a_live_row_count_drift_is_refused(
        self, markers: dict[str, Path], live: dict[str, Any], table: str, count: int
    ) -> None:
        rows = dict(registrar.RUNTIME_RAG_ROWS)
        rows[table] = count
        readers = {db: _RagConnection(rows) for db in registrar.RUNTIME_DATABASES}

        with pytest.raises(registrar.RegistrarError, match="행 수"):
            registrar.runtime_rag_provenance(readers, columns=_rag_columns())

    @pytest.mark.parametrize("dropped", ["embedding_model", "chunk_contract_sha256"])
    def test_a_field_missing_from_both_markers_is_refused(
        self, markers: dict[str, Path], live: dict[str, Any], dropped: str
    ) -> None:
        """**양쪽에서 함께 빠지면 상호 비교가 `None == None`으로 지나간다.**

        exact key 검사가 없으면 그대로 통과한다(구현리뷰 11차 필수 1).
        """

        for path in markers.values():
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload.pop(dropped)
            path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(registrar.RegistrarError, match="key 집합"):
            registrar.runtime_rag_provenance(_rag_readers(), columns=_rag_columns())

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("document_ids", ["DOC-A", "DOC-B", "DOC-C"]),
            ("chunk_schema_version", "cs0"),
            ("chunk_contract_sha256", "0" * 60),
            ("embedding_model", "other/model"),
            ("embedding_model_revision", "0" * 40),
            ("schema_sha256", "0" * 64),
            ("dimension", 768),
            ("format_version", 2),
        ],
    )
    def test_both_markers_wrong_the_same_way_is_still_refused(
        self, markers: dict[str, Path], live: dict[str, Any], field: str, value: Any
    ) -> None:
        """**양쪽이 같은 잘못된 값이면 상호 비교로는 못 잡는다**(구현리뷰 12차 필수 1).

        loader 정본 상수와 직접 대조해야 걸린다.
        """

        for path in markers.values():
            _rewrite(path, **{field: value})

        with pytest.raises(registrar.RegistrarError, match="RAG marker"):
            registrar.runtime_rag_provenance(_rag_readers(), columns=_rag_columns())

    @pytest.mark.parametrize("both", [True, False])
    def test_a_drifted_source_map_is_refused(
        self, markers: dict[str, Path], live: dict[str, Any], both: bool
    ) -> None:
        """**marker v1은 source map == corrected map이다**(구현리뷰 13차 필수 2).

        원본 provenance로 해석하지 않는 것과 값을 아예 안 보는 것은 다르다.
        """

        targets = markers.values() if both else [markers["kosa_agent"]]
        for path in targets:
            _rewrite(path, source_sha256_by_document={"DOC-X": "0" * 64})

        with pytest.raises(registrar.RegistrarError):
            registrar.runtime_rag_provenance(_rag_readers(), columns=_rag_columns())

    def test_the_v1_source_map_equals_the_corrected_map(
        self, markers: dict[str, Path]
    ) -> None:
        for path in markers.values():
            payload = json.loads(path.read_text(encoding="utf-8"))
            assert (
                payload["source_sha256_by_document"]
                == payload["corrected_sha256_by_document"]
            )
            assert payload["format_version"] == 1

    def test_the_contract_comes_from_the_loader_constants(self) -> None:
        """값을 두 곳에 적지 않는다 — 적으면 갈린다."""

        import load_rag_documents as rag

        contract = registrar.RAG_MARKER_CONTRACT
        assert contract["document_ids"] == list(rag.CANONICAL_DOCUMENT_IDS)
        assert contract["chunk_schema_version"] == rag.CHUNK_SCHEMA_VERSION
        assert contract["chunk_contract_sha256"] == rag.CHUNK_CONTRACT_SHA256
        assert contract["embedding_model"] == rag.EMBEDDING_MODEL
        assert contract["embedding_model_revision"] == rag.EMBEDDING_MODEL_REVISION
        assert contract["dimension"] == rag.EMBEDDING_DIMENSION
        assert contract["format_version"] == 1

    @pytest.mark.parametrize("broken", [1, 25])
    def test_broken_live_embeddings_are_refused(
        self, markers: dict[str, Path], live: dict[str, Any], broken: int
    ) -> None:
        """**두 DB가 똑같이 손상돼도 잡는다**(구현리뷰 12차 필수 1).

        marker의 `null_embedding_count=0`만 믿으면 손상이 새 기준으로 등록된다.
        `live_fingerprint()`는 embedding vector를 hash하지 않아 그것으로도 못 잡는다.
        """

        readers = {
            db: _RagConnection(broken_embeddings=broken)
            for db in registrar.RUNTIME_DATABASES
        }

        with pytest.raises(registrar.RegistrarError, match="embedding"):
            registrar.runtime_rag_provenance(readers, columns=_rag_columns())

    def test_both_runtime_databases_are_read(self, markers: dict[str, Path]) -> None:
        """한 DB의 marker만 보고 정답을 삼지 않는다."""

        assert set(registrar.RUNTIME_DATABASES) == {"kosa_agent", "kosa_agent_e2e"}
        assert set(markers) == set(registrar.RUNTIME_DATABASES)


@contextlib.contextmanager
def _healthy_runtime_postcheck(*, fail: bool = False) -> Any:
    """Runtime 물리 postcheck를 성공/실패로 고정한다.

    `postcheck_database()`는 constraint·index·privilege catalog 전체를 읽는다. 그
    내부 계약은 `test_agent_runtime_migration.py`가 소유하므로 여기서 catalog fake를
    다시 만들지 않는다. 이 파일이 잠그는 것은 **그것이 호출되는가**와 **실패가
    전파되는가**다.
    """

    import apply_agent_runtime as agent_runtime

    calls: list[str] = []
    real_load = agent_runtime.load_and_validate_sql
    real_post = agent_runtime.postcheck_database
    real_alarm = agent_runtime.alarm_event_count

    def _post(_connection: Any, **_kwargs: Any) -> Any:
        calls.append("postcheck")
        if fail:
            raise agent_runtime.AgentRuntimeStateError("postcheck 실패")
        return SimpleNamespace(
            signature="sig",
            schema_signature_sha256="a" * 64,
            action_rows=0,
            alarm_rows=0,
        )

    agent_runtime.load_and_validate_sql = lambda: ("sql", ())
    agent_runtime.postcheck_database = _post
    agent_runtime.alarm_event_count = lambda _c: 0
    try:
        yield calls
    finally:
        agent_runtime.load_and_validate_sql = real_load
        agent_runtime.postcheck_database = real_post
        agent_runtime.alarm_event_count = real_alarm


class TestRuntimePhysicalPostcheck:
    """**물리 postcheck는 opt-out과 무관하게 항상 한다**(구현리뷰 13차 필수 1).

    `postcheck_database()`는 marker 검사가 아니다 — constraint·FK·CHECK, index
    allowlist와 partial index, table allowlist, `PUBLIC` privilege 0건을 본다.
    boolean이 그것까지 끄면 partial index가 없거나 `PUBLIC` 권한이 남은 DB도 final
    manifest로 등록된다.
    """

    def _run(
        self, env: dict[str, str], candidate: dict[str, Any], **kwargs: Any
    ) -> Any:
        connection = _CandidateConnection("kosa_agent", candidate)
        return verifier.verify_database(
            "kosa_agent",
            "runtime_clean",
            environ=env,
            engine_factory=lambda _t: _Engine(connection),
            candidate=candidate,
            **kwargs,
        )

    @pytest.fixture()
    def runtime_candidate(
        self, markers: dict[str, Path], live: dict[str, Any]
    ) -> dict[str, Any]:
        provenance = registrar.runtime_rag_provenance(
            _rag_readers(), columns=_rag_columns()
        )
        runtime = candidates.build_profile_candidate(
            "runtime",
            source=candidates.load_source_manifest(),
            runtime_rag=provenance,
        )
        return _trim_to_empty(runtime, keep={"document", "document_chunk"})

    @pytest.mark.parametrize("require_marker", [True, False])
    def test_the_physical_postcheck_always_runs(
        self,
        env: dict[str, str],
        runtime_candidate: dict[str, Any],
        require_marker: bool,
    ) -> None:
        with _healthy_runtime_postcheck() as calls:
            # `True`면 marker 부재로 mismatch가 나지만, 그 전에 postcheck는 이미 돈다.
            with contextlib.suppress(verifier.AcceptanceMismatchError):
                self._run(env, runtime_candidate, require_runtime_marker=require_marker)

        assert calls == ["postcheck"]

    @pytest.mark.parametrize("require_marker", [True, False])
    def test_a_postcheck_failure_fails_regardless_of_the_flag(
        self,
        env: dict[str, str],
        runtime_candidate: dict[str, Any],
        require_marker: bool,
    ) -> None:
        """partial index 누락·constraint drift·`PUBLIC` 권한이 여기로 수렴한다."""

        with _healthy_runtime_postcheck(fail=True):
            with pytest.raises(verifier.AcceptanceMismatchError) as caught:
                self._run(env, runtime_candidate, require_runtime_marker=require_marker)

        kinds = {e.get("mismatch_kind") for e in caught.value.details["mismatches"]}
        assert "RUNTIME_SCHEMA" in kinds

    def test_the_sql_loader_is_deferred_with_the_marker(
        self,
        env: dict[str, str],
        runtime_candidate: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """**CM-3.2 소유 SQL artifact에도 결합되지 않는다**(구현리뷰 14차 필수 1).

        `migration_sha`는 marker를 읽을 때만 쓰인다. loader를 flag 앞에 두면 CM-1.8이
        `002_agent_runtime_clean.sql`을 무조건 읽고 문법·객체 수·guard까지 검증한다.
        "현재 파일이 우연히 유효하다"와 "의존하지 않는다"는 다른 계약이다.
        """

        import apply_agent_runtime as agent_runtime

        loaded: list[str] = []

        def _boom() -> tuple[str, tuple[()]]:
            loaded.append("sql")
            raise agent_runtime.AgentRuntimeArtifactError("SQL을 읽을 수 없습니다")

        with _healthy_runtime_postcheck():
            monkeypatch.setattr(agent_runtime, "load_and_validate_sql", _boom)

            # flag가 false면 loader를 **아예 부르지 않는다**.
            result = self._run(env, runtime_candidate, require_runtime_marker=False)
            assert result.status == verifier.STATUS_PASS
            assert loaded == []

            # true면 loader 오류가 marker 축으로 수렴한다.
            with pytest.raises(verifier.AcceptanceMismatchError) as caught:
                self._run(env, runtime_candidate, require_runtime_marker=True)

        assert loaded == ["sql"]
        kinds = {e.get("mismatch_kind") for e in caught.value.details["mismatches"]}
        assert kinds == {"RUNTIME_MARKER"}

    def test_the_physical_postcheck_precedes_any_cm32_artifact(self) -> None:
        """소유권 순서를 코드 구조로 고정한다."""

        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(verifier.verify_database)))
        calls = [
            (node.lineno, ast.unparse(node.func))
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        ]
        postcheck = min(
            line for line, name in calls if name.endswith("postcheck_database")
        )
        loader = min(
            line for line, name in calls if name.endswith("load_and_validate_sql")
        )

        assert postcheck < loader

    def test_only_the_marker_check_is_deferred(
        self,
        env: dict[str, str],
        runtime_candidate: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """**marker만 미룬다.** 없어도 `False`면 PASS, `True`면 실패."""

        import apply_agent_runtime as agent_runtime

        seen: list[str] = []
        monkeypatch.setattr(
            agent_runtime,
            "load_marker",
            lambda *a, **k: seen.append("marker") or None,
        )

        with _healthy_runtime_postcheck():
            result = self._run(env, runtime_candidate, require_runtime_marker=False)
        assert result.status == verifier.STATUS_PASS
        assert seen == []

        with _healthy_runtime_postcheck():
            with pytest.raises(verifier.AcceptanceMismatchError) as caught:
                self._run(env, runtime_candidate, require_runtime_marker=True)

        assert seen == ["marker"]
        kinds = {e.get("mismatch_kind") for e in caught.value.details["mismatches"]}
        assert kinds == {"RUNTIME_MARKER"}


class TestRuntimeMarkerOwnership:
    """**CM-1.8 ↔ CM-3.2 순환을 끊은 경계**(구현리뷰 12차 필수 2).

    `agent_runtime` marker는 "final migration이 적용됐다"는 증명서이고 그 적용·발급은
    `V5-CM-3.2`가 소유한다. CM-1.8은 manifest 내용만 판정한다.
    """

    def test_the_registrar_does_not_require_the_runtime_marker(self) -> None:
        """registrar가 candidate 검증에서 marker를 요구하지 않는다."""

        calls: list[dict[str, Any]] = []
        bundle = candidates.build_final_bundle(runtime_rag=GOOD_RAG)
        registrar.verify_bundle(bundle, verify=_ok_verify(calls))

        for call in calls:
            assert call["require_runtime_marker"] is False

    def test_the_marker_check_still_exists_for_full_acceptance(self) -> None:
        """**경계를 없앤 것이 아니다.** 기본값은 여전히 요구한다."""

        import inspect

        signature = inspect.signature(verifier.verify_database)
        assert signature.parameters["require_runtime_marker"].default is True

    def test_cm32_took_the_marker_ownership(self) -> None:
        """CM-1.6이 넘기고 **CM-3.2가 받았다.**

        이 테스트는 원래 `run_apply()`가 `FINAL_RUNTIME_MIGRATION_NOT_WIRED`로 막혀
        있음을 단언했다. 그 차단 해제가 곧 CM-3.2의 일이므로, 지금은 **소유권이 실제로
        옮겨졌는지**를 본다 — 인계가 열린 채 잊히지 않게 하는 것이 이 테스트의 목적이다.

        공용 DB에 붙지 않는다. engine factory를 주입해 도달 여부만 본다.
        """

        import apply_agent_runtime as agent_runtime
        from db_target import BootstrapTarget

        target = BootstrapTarget(
            host="shared.example",
            port=5432,
            username="reader",
            password="hidden",
            database="kosa_agent",
            profile="runtime",
        )

        class _Reached(Exception):
            pass

        def _engine(_target: object) -> object:
            raise _Reached

        with pytest.raises(_Reached):
            agent_runtime.run_apply(
                target, change_reference="TEST-1", engine_factory=_engine
            )

        assert agent_runtime.TASK_ID == "V5-CM-3.2"
        assert agent_runtime.FINAL_ARTIFACT_TYPE == "agent_runtime_final"

    def test_the_runtime_marker_path_is_the_final_lineage(self) -> None:
        """CM-3.2가 발급할 marker는 **구 계보와 파일명이 겹치지 않는다.**

        `V5-CM-1.2`가 격리한 `runtime_clean.<database>.json`을 재사용하면 history의
        폐기 marker를 final로 잘못 복원·승격하는 사고가 구조적으로 가능해진다.
        """

        import apply_agent_runtime as agent_runtime

        for database in registrar.RUNTIME_DATABASES:
            path = agent_runtime.marker_path(database)
            assert path.name == f"agent_runtime_final.{database}.json"
            assert path.name != f"runtime_clean.{database}.json"


class TestPublicAccessGuard:
    """**CLI는 두 축이다**(팀장 결정 2026-08-24 · 구현리뷰 12차 필수 4).

    - `--read-public` — 공용 DB **조회** 승인
    - `--confirm` — 저장소 **쓰기** 승인

    candidate의 Runtime RAG hash를 fresh live에서 만들어야 하므로 no-op 판정도 DB를
    읽는다. 조기접속 방지는 조회 축이 담당한다.

    `.env`에 자격증명이 있으면 아무 생각 없이 실행한 CLI가 그대로 공용 서버에 닿는다.
    실제로 그 사고가 있었다(구현보고 §4.2).
    """

    def test_without_the_flag_no_connection_is_created(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(**_kwargs: Any) -> None:
            raise AssertionError("flag 없이 공용 DB에 연결했다")

        monkeypatch.setattr(registrar, "_measure_runtime_rag_from_live", _boom)

        with pytest.raises(registrar.RegistrarError, match="--read-public"):
            registrar.run([], verify=_ok_verify([]))

    def test_the_flag_exists_and_is_not_default(self) -> None:
        parser = registrar._parser()
        args = parser.parse_args([])

        assert args.read_public is False
        assert parser.parse_args(["--read-public"]).read_public is True

    @pytest.mark.parametrize(
        ("argv", "reads_db", "writes_repo"),
        [
            ([], False, False),
            (["--confirm"], False, True),
            (["--read-public"], True, False),
            (["--read-public", "--confirm"], True, True),
        ],
    )
    def test_the_two_axes_are_independent(
        self, argv: list[str], reads_db: bool, writes_repo: bool
    ) -> None:
        """두 축이 각각 무엇을 여는지 표로 고정한다."""

        parser = registrar._parser()
        args = parser.parse_args(argv)

        assert args.read_public is reads_db
        assert args.confirm is writes_repo

    def test_the_injected_path_needs_no_flag(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """테스트 주입 경로는 DB를 열지 않으므로 flag가 필요 없다."""

        code = registrar.run([], verify=_ok_verify([]), runtime_rag=GOOD_RAG)

        assert code == registrar.EXIT_CONFIRM_REQUIRED


class TestConnectionLifetime:
    """live 초기화 실패 지점과 무관하게 connection이 닫힌다(구현리뷰 12차 필수 3)."""

    @pytest.mark.parametrize("fail_at", [0, 1])
    def test_every_created_connection_is_closed(
        self, monkeypatch: pytest.MonkeyPatch, fail_at: int
    ) -> None:
        closed: list[str] = []
        disposed: list[str] = []
        created: list[str] = []

        class _Conn:
            def __init__(self, name: str) -> None:
                self.name = name

            def begin(self) -> None:
                if created.index(self.name) == fail_at:
                    raise RuntimeError("초기화 실패")

            def close(self) -> None:
                closed.append(self.name)

        class _Eng:
            def __init__(self, name: str) -> None:
                self.name = name

            def connect(self) -> _Conn:
                created.append(self.name)
                return _Conn(self.name)

            def dispose(self) -> None:
                disposed.append(self.name)

        names = iter(registrar.RUNTIME_DATABASES)
        monkeypatch.setattr(verifier, "load_bootstrap_target", lambda db, **_k: db)
        monkeypatch.setattr(verifier, "_engine_for", lambda db: _Eng(db))
        monkeypatch.setattr(verifier, "_sql", lambda *a, **k: None)
        monkeypatch.setattr(verifier, "_validate_read_identity", lambda *a, **k: None)

        with pytest.raises(RuntimeError):
            registrar._measure_runtime_rag_from_live(environ={})

        # `ExitStack`은 역순으로 닫는다 — 순서가 아니라 **한 번씩 전부**가 계약이다.
        assert sorted(closed) == sorted(created), (closed, created)
        assert sorted(disposed) == sorted(created)
        assert len(created) == fail_at + 1
        del names


class TestCliBoundary:
    def test_a_known_error_becomes_a_sanitized_exit(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """**traceback·절대경로를 내보내지 않는다**(구현리뷰 10차 필수 3)."""

        def _boom(**_kwargs: Any) -> None:
            raise candidates.CandidateError("RAG_PROVENANCE_MISMATCH")

        monkeypatch.setattr(registrar, "_measure_runtime_rag_from_live", _boom)

        code = registrar.main(["--read-public"])
        out = capsys.readouterr().out

        assert code == registrar.EXIT_USAGE
        payload = json.loads(out)
        assert payload == {"status": "FAIL", "reason_code": "CANDIDATE_CONTRACT"}
        for marker in ("Traceback", "/Users/", "postgresql://", "password"):
            assert marker not in out, marker

    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            ("RegistrarError", "REGISTRAR_CONTRACT"),
            ("NotRegisteredError", "NOT_REGISTERED"),
            ("ArtifactMismatchError", "ARTIFACT_MISMATCH"),
            ("ManifestMetadataError", "INVALID_METADATA"),
        ],
    )
    def test_each_known_error_maps_to_a_stable_reason(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        error: str,
        reason: str,
    ) -> None:
        cls = getattr(registrar, error, None) or getattr(manifest_v3, error)

        def _boom(**_kwargs: Any) -> None:
            raise cls("x")

        monkeypatch.setattr(registrar, "_measure_runtime_rag_from_live", _boom)
        registrar.main(["--read-public"])

        assert json.loads(capsys.readouterr().out)["reason_code"] == reason

    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            ("sqlalchemy", "CONNECT_OR_QUERY_FAILED"),
            ("oserror", "REGISTRAR_IO"),
            ("normalization", "CONNECT_OR_QUERY_FAILED"),
        ],
    )
    def test_operational_errors_are_sanitized(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        error: str,
        reason: str,
    ) -> None:
        """**연결·권한·디스크 오류는 발급에서 흔하다**(구현리뷰 12차 필수 3)."""

        from sqlalchemy.exc import OperationalError
        from value_normalization import ValueNormalizationError

        def _boom(**_kwargs: Any) -> None:
            if error == "sqlalchemy":
                raise OperationalError(
                    "SELECT 1", {}, Exception("postgresql://u:p@kosa165/kosa")
                )
            if error == "oserror":
                raise OSError("/Users/person/repo/infra/bootstrap/manifests")
            raise ValueNormalizationError("JSON 값 형식이 잘못됐습니다")

        monkeypatch.setattr(registrar, "_measure_runtime_rag_from_live", _boom)

        code = registrar.main(["--read-public"])
        captured = capsys.readouterr()

        assert code == registrar.EXIT_UNVERIFIABLE
        assert json.loads(captured.out) == {"status": "FAIL", "reason_code": reason}
        for stream in (captured.out, captured.err):
            for marker in (
                "Traceback",
                "postgresql://",
                "/Users/",
                "kosa165",
                "password",
            ):
                assert marker not in stream, (marker, stream[:120])

    def test_an_unexpected_error_is_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """programmer error를 성공이나 일반 mismatch로 바꾸지 않는다."""

        def _boom(**_kwargs: Any) -> None:
            raise ZeroDivisionError("programmer error")

        monkeypatch.setattr(registrar, "_measure_runtime_rag_from_live", _boom)

        with pytest.raises(ZeroDivisionError):
            registrar.main(["--read-public"])

    def test_the_entrypoint_loads_dotenv_without_override(self) -> None:
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(registrar.main)))
        loads = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and ast.unparse(node.func) == "load_dotenv"
            and any(
                kw.arg == "override" and kw.value.value is False for kw in node.keywords
            )
        ]

        assert len(loads) == 1
