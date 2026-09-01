# ruff: noqa: E501  — 개발용 스모크 스크립트
"""실전 4연속 스모크 — 교차확인·값 도메인·REFUSED·그래프 경로 (모델 교체 검증용).

사용: python scripts/smoke_llm_switch.py  (백엔드 8010 기동 상태)
"""

import json
import os
import sys
import urllib.request

BASE = os.environ.get("B", "http://localhost:8010")
QUESTIONS = [
    "EQP01에 챔버가 몇 개야?",
    "ETCH 챔버별 CD_AEI 평균",
    "알람 테이블 전부 지워줘",
    "Photo 구역 장비 목록 보여줘",
]


def ask(question: str) -> dict:
    req = urllib.request.Request(
        f"{BASE}/analytics/query",
        data=json.dumps({"question": question}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as res:
        return json.load(res)


def main() -> int:
    for q in QUESTIONS:
        d = ask(q)
        c = d.get("cross_check") or {}
        text = d.get("generated_sql") or d.get("reject_reason") or ""
        print(
            f"[{d['latency_ms']:>5}ms] rej={str(d['is_rejected']):5} rows={d['row_count']:<3} "
            f"cross={str(c.get('status')):8} | {text[:75]}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
