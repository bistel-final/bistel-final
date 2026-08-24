"""fdc_final(로컬 검증용 DB)에서 Detection 재계산을 바로 확인하는 CLI.

`backend/app/detection/repository.py`·`service.py`는 SQLAlchemy `Connection` 하나만
받으면 동작한다. 이 스크립트는 `app.common.config`(팀 공용 `.env` — `POSTGRES_DB=
kosa_agent`, `NEO4J_*`·`N8N_WEBHOOK_URL` 등 이 검증과 무관한 값까지 전부 필수로
요구한다)를 거치지 않고, 커맨드라인 인자로 접속 정보를 직접 받는다. 팀 `.env`를
건드리지 않고 로컬에 따로 올려둔 `fdc_final` 같은 DB를 바로 검증하려는 용도다.

01-project-rules.md 1절 금지 5: 비밀번호·API Key·전체 DSN을 로그·stdout에 출력하지
않는다. 비밀번호는 `--password` 인자로 주지 않는 게 원칙이다(쉘 history에 남는다) —
`FDC_TEST_DB_PASSWORD` 환경변수나 대화형 프롬프트(getpass, 화면에 안 찍힘)로만 받고,
출력에는 host·port·db 이름까지만 남긴다.

V5-A-1.1~V5-A-1.4 완료 기준 자체가 무엇인지는 `app/detection/service.py`의
`verify_summary_recalculation`·`verify_evaluation_recalculation`·
`verify_alarm_reproduction`·`derive_r03_events`·`persist_r03_alarms` docstring을
본다. 이 스크립트는 그 함수들을 호출해서 사람이 읽기 좋은 리포트로 출력만 한다 —
업무 판정 로직은 여기에 없다.

V5-A-1.1(Summary)·V5-A-1.2(evaluation)·V5-A-1.3(TRACE·SUMMARY 알람)은 전부
읽기 전용 재현·대조라 옵션 없이 항상 실행한다. V5-A-1.4(R03)만 다르다 —
`r03_alarm_history`에 실제 INSERT가 일어나는 유일한 단계라서, 기본값은
`derive_r03_events`(계산만, 저장 안 함)로 "몇 건이 나올지"만 먼저 보여주고,
`--persist-r03` 플래그를 줘야만 `persist_r03_alarms`(실제 INSERT + 이 스크립트가
commit)까지 실행한다. `ON CONFLICT ... DO NOTHING`으로 멱등하게 짜여 있어서
`--persist-r03`를 여러 번 줘도 안전하다(두 번째부터는 신규 INSERT 0건).

또한 `r03_alarm_history` 테이블 자체는 이 스크립트나 A의 코드가 만드는 게
아니라 `V5-CM-3.1` 마이그레이션
(`backend/migrations/v5/001_reference_extensions_final.sql`)이
미리 만들어둬야 한다 — 그 마이그레이션이 아직 적용 안 된 DB에서
`--persist-r03`를 주면 "relation r03_alarm_history does not exist" 오류가 난다.

사용 예 (Windows, backend venv 활성화 후):

    cd backend
    venv\\Scripts\\activate
    set FDC_TEST_DB_PASSWORD=본인_로컬_비밀번호
    python scripts\\verify_detection_recalculation.py --host localhost --port 5432 --db fdc_final --user postgres
    REM R03까지 실제로 적재하려면:
    python scripts\\verify_detection_recalculation.py --host localhost --port 5432 --db fdc_final --user postgres --persist-r03

`--password`를 생략하고 `FDC_TEST_DB_PASSWORD`도 안 정해두면 실행 중 비밀번호를
대화형으로 물어본다(입력값이 화면에 안 찍힌다).

종료 코드: 실행한 모든 검증(기본은 A-1.1~1.4 dry-run, `--persist-r03`를 주면
A-1.4 적재까지)이 전부 통과하면 0, 하나라도 실패하면 1을 반환한다. CI나 스크립트에서
`if` 분기에 바로 쓸 수 있다.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

# scripts/ 에서 바로 실행해도 `app.*` 를 import 할 수 있도록 backend/ 를
# sys.path 맨 앞에 추가한다. (backend/scripts/*.py 들이 공유하는 관례다.)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.common.enums import AlarmType
from app.detection import service
from app.detection.service import (
    AlarmReproductionResult,
    EvaluationVerificationResult,
    R03DerivationResult,
    R03PersistResult,
    SummaryAlarmVerificationResult,
    SummaryVerificationResult,
    TraceAlarmVerificationResult,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Connection


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "지정한 PostgreSQL DB(기본 fdc_final)에서 V5-A-1.1(Summary)·"
            "V5-A-1.2(evaluation)·V5-A-1.3(TRACE·SUMMARY 알람)·V5-A-1.4(R03) "
            "재계산·재현을 검증한다."
        )
    )
    parser.add_argument("--host", default="localhost", help="기본값: localhost")
    parser.add_argument("--port", type=int, default=5432, help="기본값: 5432")
    parser.add_argument("--db", default="fdc_final", help="기본값: fdc_final")
    parser.add_argument("--user", required=True, help="DB 접속 계정")
    parser.add_argument(
        "--password",
        default=None,
        help=(
            "권장하지 않음(쉘 history에 남는다). 생략하면 FDC_TEST_DB_PASSWORD "
            "환경변수, 그것도 없으면 대화형으로 입력받는다."
        ),
    )
    parser.add_argument(
        "--max-mismatches",
        type=int,
        default=10,
        help="화면에 출력할 불일치 항목 최대 개수(기본 10). 전체 개수는 항상 출력한다.",
    )
    parser.add_argument(
        "--persist-r03",
        action="store_true",
        help=(
            "V5-A-1.4 R03을 실제로 r03_alarm_history에 INSERT하고 이 스크립트가 "
            "commit한다(기본은 dry-run — 계산만 하고 저장하지 않는다). "
            "V5-CM-3.1 마이그레이션이 이미 적용된 DB에서만 준다. "
            "멱등(ON CONFLICT DO NOTHING)하므로 여러 번 줘도 안전하다."
        ),
    )
    return parser.parse_args()


def _resolve_password(cli_password: str | None) -> str:
    if cli_password:
        return cli_password

    env_password = os.getenv("FDC_TEST_DB_PASSWORD")
    if env_password:
        return env_password

    # getpass.getpass(): 입력을 터미널에 그대로 찍지 않는다(비밀번호 입력창처럼 동작).
    return getpass.getpass("DB 비밀번호 입력: ")


def _print_summary_report(result: SummaryVerificationResult, max_mismatches: int) -> bool:
    print("\n=== V5-A-1.1 Summary 재계산 (summary_data 대조) ===")
    print(f"재계산 그룹 수: {result.recomputed_count}  (reference: {result.reference_count})")
    print(f"missing_keys(재계산에 없는데 reference엔 있음): {len(result.missing_keys)}")
    print(f"unexpected_keys(재계산엔 있는데 reference에 없음): {len(result.unexpected_keys)}")
    print(f"mismatches(값이 다름): {len(result.mismatches)}")

    for mismatch in result.mismatches[:max_mismatches]:
        print(
            f"  - {mismatch.key}: {mismatch.field} "
            f"recomputed={mismatch.recomputed} reference={mismatch.reference} "
            f"diff={mismatch.diff}"
        )
    if len(result.mismatches) > max_mismatches:
        print(f"  ... 외 {len(result.mismatches) - max_mismatches}건 더 있음")

    print(f"결과: {'PASS' if result.ok else 'FAIL'}")
    return result.ok


def _print_evaluation_report(result: EvaluationVerificationResult, max_mismatches: int) -> bool:
    print("\n=== V5-A-1.2 evaluation 재현 (evaluation 대조) ===")
    print(f"재계산 그룹 수: {result.recomputed_count}  (reference: {result.reference_count})")
    print(f"missing_keys(재계산에 없는데 reference엔 있음): {len(result.missing_keys)}")
    print(f"unexpected_keys(재계산엔 있는데 reference에 없음): {len(result.unexpected_keys)}")
    print(f"mismatches(값이 다름): {len(result.mismatches)}")

    for mismatch in result.mismatches[:max_mismatches]:
        print(
            f"  - {mismatch.key}: {mismatch.field} "
            f"recomputed={mismatch.recomputed} reference={mismatch.reference}"
        )
    if len(result.mismatches) > max_mismatches:
        print(f"  ... 외 {len(result.mismatches) - max_mismatches}건 더 있음")

    counts = result.recomputed_alarm_type_counts
    print(
        "등급별 그룹 수: "
        f"IN={counts.get(AlarmType.IN, 0)}(기준 4,538) / "
        f"OOC={counts.get(AlarmType.OOC, 0)}(기준 216) / "
        f"OOS={counts.get(AlarmType.OOS, 0)}(기준 46)"
    )
    print(f"TRACE alarm 후보(OOS point 합): {result.recomputed_trace_alarm_count}(기준 138)")

    print(f"내부 일치(ok — 재계산과 evaluation 테이블이 서로 일치): {'PASS' if result.ok else 'FAIL'}")
    print(
        "최종 수용값(IN 4,538/OOC 216/OOS 46/TRACE 138) 일치: "
        f"{'PASS' if result.matches_acceptance_values else 'FAIL'}"
    )
    return result.ok and result.matches_acceptance_values


def _print_trace_alarm_report(result: TraceAlarmVerificationResult) -> bool:
    # TraceAlarmVerificationResult는 Summary·evaluation과 달리 그룹별 mismatches
    # 리스트가 없다 — "OOS point 합" vs "저장된 총 건수"라는 숫자 하나만 대조하는
    # 대상이라서다(trace_alarm_history 한 행 한 행을 재계산 결과와 짝짓지 않는다).
    print("\n=== V5-A-1.3 TRACE 알람 재현 (trace_alarm_history 대조) ===")
    print(
        f"재계산(OOS point 합): {result.recomputed_count}  "
        f"(reference 저장 건수: {result.reference_count})"
    )
    print(f"occurred_at NULL 건수: {result.reference_occurred_at_null_count}(기준 0)")
    print(f"내부 일치(ok): {'PASS' if result.ok else 'FAIL'}")
    trace_pass = "PASS" if result.matches_acceptance_value else "FAIL"
    print(f"수용값(TRACE 138) 일치: {trace_pass}")
    return result.ok and result.matches_acceptance_value


def _print_summary_alarm_report(result: SummaryAlarmVerificationResult) -> bool:
    print("\n=== V5-A-1.3 SUMMARY 알람 재현 (동적 CL±3σ) ===")
    print(
        f"재계산(관리한계 이탈 그룹 수): {result.recomputed_count}  "
        f"(reference 저장 건수: {result.reference_count})"
    )
    print(f"occurred_at NULL 건수: {result.reference_occurred_at_null_count}(기준 0)")
    print(f"내부 일치(ok): {'PASS' if result.ok else 'FAIL'}")
    summary_pass = "PASS" if result.matches_acceptance_value else "FAIL"
    print(f"수용값(SUMMARY 51) 일치: {summary_pass}")
    return result.ok and result.matches_acceptance_value


def _print_alarm_reproduction_report(result: AlarmReproductionResult) -> bool:
    # TRACE·SUMMARY 각각의 리포트를 출력한 뒤, 둘을 합친 합계(수용값 189)만 이
    # 함수에서 추가로 확인한다 — 개별 판정은 이미 위 두 함수가 반환한 bool로 끝났다.
    trace_ok = _print_trace_alarm_report(result.trace)
    summary_ok = _print_summary_alarm_report(result.summary)
    print(
        f"\n저장 알람 합계(TRACE+SUMMARY): {result.total_stored_alarms}(기준 189) "
        f"{'PASS' if result.total_stored_alarms == 189 else 'FAIL'}"
    )
    return trace_ok and summary_ok and result.total_stored_alarms == 189


def _print_r03_derivation_report(result: R03DerivationResult) -> bool:
    # dry-run이다 — derive_r03_events(service.py)는 readonly 조회·순수 계산만
    # 하고 r03_alarm_history에는 아무것도 쓰지 않는다.
    print("\n=== V5-A-1.4 R03 파생 (dry-run, 아직 저장하지 않음) ===")
    print(f"연속 3 OOS로 찾은 R03 이벤트 수: {result.count}(기준 3)")
    for event in result.events:
        owner = event.owner
        print(
            f"  - chamber={event.chamber_id} parameter={event.parameter_id} "
            f"step={event.recipe_step_no} owner_lot_hist_id={owner.lot_hist_id} "
            f"occurred_at={event.occurred_at}"
        )
    r03_derive_pass = "PASS" if result.matches_acceptance_value else "FAIL"
    print(f"수용값(R03 3건) 일치: {r03_derive_pass}")
    return result.matches_acceptance_value


def _print_r03_persist_report(result: R03PersistResult) -> bool:
    print("\n=== V5-A-1.4 R03 적재 (실제 INSERT 수행, 이 스크립트가 commit함) ===")
    print(f"이번 계산으로 찾은 R03 이벤트 수: {result.derived_count}(기준 3)")
    print(
        f"이번 호출로 새로 INSERT된 행 수: {result.inserted_count}"
        "(처음 실행이면 3, 재실행이면 0이어야 멱등 적재가 맞는 것)"
    )
    print(
        f"INSERT 이후 r03_alarm_history 전체 행 수: {result.total_count_after}(기준 3)"
    )
    print(
        "수용값(r03_alarm_history 3건) 일치: "
        f"{'PASS' if result.matches_acceptance_value else 'FAIL'}"
    )
    return result.matches_acceptance_value


def _run_checks(connection: Connection, max_mismatches: int, persist_r03: bool) -> bool:
    summary_ok = _print_summary_report(
        service.verify_summary_recalculation(connection), max_mismatches
    )
    evaluation_ok = _print_evaluation_report(
        service.verify_evaluation_recalculation(connection), max_mismatches
    )
    alarm_result = service.verify_alarm_reproduction(connection)
    alarm_ok = _print_alarm_reproduction_report(alarm_result)

    if persist_r03:
        # readonly_connection·writer_connection 자리에 같은 connection을 그대로
        # 넘긴다 — service.persist_r03_alarms docstring이 명시한 대로, role
        # 구분이 없는 로컬 개발 환경에서는 이렇게 써도 동작한다(팀 운영 환경의
        # 최소권한 role 분리는 이 스크립트가 아니라 app.common.db가 담당).
        persist_result = service.persist_r03_alarms(connection, connection)
        r03_ok = _print_r03_persist_report(persist_result)
        # INSERT는 여기서 커밋해야 실제로 남는다 — repository.insert_r03_alarms는
        # 트랜잭션 경계를 호출자(=이 스크립트)에게 넘겨준다고 명시했으므로, 이
        # 스크립트가 그 책임을 진다. commit을 안 하면 `with engine.connect()`
        # 블록이 끝날 때 조용히 롤백된다.
        connection.commit()
    else:
        r03_ok = _print_r03_derivation_report(service.derive_r03_events(connection))

    return summary_ok and evaluation_ok and alarm_ok and r03_ok


def main() -> int:
    args = _parse_args()
    password = _resolve_password(args.password)

    url = URL.create(
        drivername="postgresql+psycopg",
        username=args.user,
        password=password,
        host=args.host,
        port=args.port,
        database=args.db,
    )
    # 접속 로그는 host·port·db까지만 남긴다 — 계정명·비밀번호·전체 DSN은 출력하지 않는다.
    print(f"[connect] host={args.host} port={args.port} db={args.db}")

    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            all_ok = _run_checks(connection, args.max_mismatches, args.persist_r03)
    finally:
        engine.dispose()

    print(f"\n=== 전체 결과: {'PASS' if all_ok else 'FAIL'} ===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
