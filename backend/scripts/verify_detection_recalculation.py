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

V5-A-1.1·V5-A-1.2 완료 기준 자체가 무엇인지는 `app/detection/service.py`
`verify_summary_recalculation`·`verify_evaluation_recalculation`의 docstring을 본다.
이 스크립트는 그 두 함수를 호출해서 사람이 읽기 좋은 리포트로 출력만 한다 — 업무
판정 로직은 여기에 없다.

사용 예 (Windows, backend venv 활성화 후):

    cd backend
    venv\\Scripts\\activate
    set FDC_TEST_DB_PASSWORD=본인_로컬_비밀번호
    python scripts\\verify_detection_recalculation.py --host localhost --port 5432 --db fdc_final --user postgres

`--password`를 생략하고 `FDC_TEST_DB_PASSWORD`도 안 정해두면 실행 중 비밀번호를
대화형으로 물어본다(입력값이 화면에 안 찍힌다).

종료 코드: 두 검증이 전부 통과(summary ok, evaluation ok + 수용값 일치)하면 0,
하나라도 실패하면 1을 반환한다. CI나 스크립트에서 `if` 분기에 바로 쓸 수 있다.
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

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Connection

from app.common.enums import AlarmType
from app.detection import service
from app.detection.service import EvaluationVerificationResult, SummaryVerificationResult


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "지정한 PostgreSQL DB(기본 fdc_final)에서 V5-A-1.1(Summary)·"
            "V5-A-1.2(evaluation) 재계산을 검증한다."
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


def _run_checks(connection: Connection, max_mismatches: int) -> bool:
    summary_ok = _print_summary_report(
        service.verify_summary_recalculation(connection), max_mismatches
    )
    evaluation_ok = _print_evaluation_report(
        service.verify_evaluation_recalculation(connection), max_mismatches
    )
    return summary_ok and evaluation_ok


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
            all_ok = _run_checks(connection, args.max_mismatches)
    finally:
        engine.dispose()

    print(f"\n=== 전체 결과: {'PASS' if all_ok else 'FAIL'} ===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
