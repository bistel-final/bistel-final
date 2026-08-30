"""Cypher 안전 검증 — Text2Cypher 의 실행 전 관문 (B파트 합의 조건 #238 의 코드화).

SQL 검증기(sql_validator)와 같은 위치·같은 철학의 미러다:
    1. 단일 문장만 — 세미콜론 다중 문장 차단
    2. 읽기 전용 강제 — MATCH/OPTIONAL MATCH 로 시작, RETURN 필수,
       쓰기·스키마 구문(CREATE·MERGE·DELETE·DETACH·SET·REMOVE·DROP·FOREACH·
       LOAD·USING·INDEX·CONSTRAINT) 차단
    3. 프로시저 차단 — CALL·apoc.*·dbms.*·db.* (Neo4j 판 카탈로그·확장 우회)
    4. 라벨 allowlist — neo4j.graph.json manifest 의 label_distribution 기준
    5. 관계 타입 allowlist — 같은 manifest 의 relationship_type_distribution 기준
    6. LIMIT 강제 — 없으면 500 주입, 초과면 500 축소

정식 Python Cypher 파서가 없어 보수적 토큰 검사로 판정한다. fail-closed:
manifest 를 읽지 못하면 전체 거부, 판단이 불확실한 구문은 거부한다.
검증 통과본(normalized_cypher)만 실행기로 넘어간다.

B 합의 조건 중 이 모듈 밖에서 지켜지는 것: 사용자 Cypher 입력 UI 없음(프론트),
credential 비노출(.env 전용), backend 실행(라우터), write/load 없음(읽기 계정).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
GRAPH_MANIFEST_PATH = (
    REPOSITORY_ROOT / "infra" / "bootstrap" / "manifests" / "neo4j.graph.json"
)

_MAX_ROWS = 500

#: 화면·이력에 노출되는 검사 항목 키 (SQL validator 의 CHECK_KEYS 미러)
CYPHER_CHECK_KEYS: tuple[str, ...] = (
    "single_statement",
    "read_only_clauses",
    "no_procedure_call",
    "allowed_labels",
    "allowed_relations",
    "limit_enforced",
)

#: 쓰기·스키마 변경 계열 — 등장 즉시 거부 (B 합의: MATCH/RETURN 외 차단)
_WRITE_KEYWORDS = (
    "create",
    "merge",
    "delete",
    "detach",
    "set",
    "remove",
    "drop",
    "foreach",
    "load",
    "using",
    "index",
    "constraint",
)
#: 실행 계획 노출·프로파일링도 조회 외 동작이라 차단
_META_KEYWORDS = ("explain", "profile")

_LABEL_RE = re.compile(
    r"\(\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*([A-Za-z_][A-Za-z0-9_]*)|\(\s*:\s*([A-Za-z_][A-Za-z0-9_]*)"
)
_RELATION_RE = re.compile(
    r"\[\s*[A-Za-z_]?[A-Za-z0-9_]*\s*:\s*([A-Za-z_][A-Za-z0-9_]*)"
)
_LIMIT_TAIL_RE = re.compile(r"\blimit\s+(\d+)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class CypherCheckResult:
    key: str
    passed: bool


@dataclass(frozen=True)
class CypherValidationResult:
    valid: bool
    normalized_cypher: str | None = None
    reason: str | None = None
    checks: tuple[CypherCheckResult, ...] = field(default_factory=tuple)


def _fail(failed_key: str, reason: str, passed: set[str]) -> CypherValidationResult:
    checks = tuple(
        CypherCheckResult(key=key, passed=(key in passed)) for key in CYPHER_CHECK_KEYS
    )
    return CypherValidationResult(
        valid=False, normalized_cypher=None, reason=reason, checks=checks
    )


@lru_cache(maxsize=1)
def _graph_allowlists() -> tuple[frozenset[str], frozenset[str]] | None:
    """graph manifest 에서 라벨·관계 타입 allowlist 를 읽는다. 실패 시 None.

    B파트 합의: neo4j.graph.json 이 그래프 스키마 정본이다. 이 파일이 곧
    allowlist 계약이므로 별도 하드코딩 목록을 두지 않는다 — 스키마가 바뀌면
    (B가 통지) manifest 갱신만으로 검증이 따라간다.
    """
    try:
        payload = json.loads(GRAPH_MANIFEST_PATH.read_text(encoding="utf-8"))
        labels = frozenset(payload["label_distribution"].keys())
        relations = frozenset(payload["relationship_type_distribution"].keys())
    except Exception:
        return None
    if not labels or not relations:
        return None
    return labels, relations


def _strip_strings_and_comments(cypher: str) -> str | None:
    """문자열 리터럴·주석을 제거한 검사용 본문을 만든다. 해석 불가면 None.

    키워드 검사가 문자열 안 단어(예: WHERE c.name = 'CREATE ROOM')에
    오탐하지 않게 하기 위함이다. 닫히지 않은 따옴표·주석은 fail-closed.
    """
    out: list[str] = []
    i, n = 0, len(cypher)
    while i < n:
        ch = cypher[i]
        if ch in ("'", '"'):
            end = cypher.find(ch, i + 1)
            while end != -1 and cypher[end - 1] == "\\":
                end = cypher.find(ch, end + 1)
            if end == -1:
                return None
            out.append(" ")
            i = end + 1
        elif cypher.startswith("//", i):
            end = cypher.find("\n", i)
            i = n if end == -1 else end
        elif cypher.startswith("/*", i):
            end = cypher.find("*/", i)
            if end == -1:
                return None
            i = end + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def validate_cypher(cypher: str) -> CypherValidationResult:
    """Cypher 한 건을 검증한다. 어떤 입력에도 예외를 던지지 않는다."""
    passed: set[str] = set()

    stripped = (cypher or "").strip().rstrip(";").strip()
    if not stripped:
        return _fail("single_statement", "Cypher 문장이 없다.", passed)

    body = _strip_strings_and_comments(stripped)
    if body is None:
        return _fail(
            "single_statement",
            "Cypher 구문을 해석할 수 없다 (닫히지 않은 문자열·주석).",
            passed,
        )

    # ── 1. 단일 문장 ───────────────────────────────────────────────────
    if ";" in body:
        return _fail("single_statement", "다중 문장은 허용되지 않는다.", passed)
    passed.add("single_statement")

    lowered = body.lower()
    words = set(re.findall(r"[a-z_]+", lowered))

    # ── 2. 읽기 전용 강제 ──────────────────────────────────────────────
    leading = lowered.split(maxsplit=1)[0] if lowered.split() else ""
    if leading not in {"match", "optional"}:
        return _fail(
            "read_only_clauses",
            "MATCH 로 시작하는 조회만 허용된다.",
            passed,
        )
    for keyword in _WRITE_KEYWORDS + _META_KEYWORDS:
        if keyword in words:
            return _fail(
                "read_only_clauses",
                f"조회 외 구문은 허용되지 않는다: {keyword.upper()}",
                passed,
            )
    if "return" not in words:
        return _fail("read_only_clauses", "RETURN 절이 필요하다.", passed)
    passed.add("read_only_clauses")

    # ── 3. 프로시저 차단 ───────────────────────────────────────────────
    if "call" in words or re.search(r"\b(apoc|dbms|db)\s*\.", lowered):
        return _fail(
            "no_procedure_call",
            "프로시저 호출(CALL·apoc·dbms)은 허용되지 않는다.",
            passed,
        )
    passed.add("no_procedure_call")

    # ── 4·5. 라벨·관계 allowlist (manifest 정본, fail-closed) ──────────
    allowlists = _graph_allowlists()
    if allowlists is None:
        return _fail(
            "allowed_labels",
            "그래프 스키마 정본(neo4j.graph.json)을 읽을 수 없어 검증을 "
            "수행할 수 없다.",
            passed,
        )
    allowed_labels, allowed_relations = allowlists

    for match in _LABEL_RE.finditer(body):
        label = match.group(1) or match.group(2)
        if label and label not in allowed_labels:
            return _fail(
                "allowed_labels",
                f"허용되지 않은 라벨이다: {label}."
                f" 사용 가능: {', '.join(sorted(allowed_labels))}",
                passed,
            )
    passed.add("allowed_labels")

    for match in _RELATION_RE.finditer(body):
        relation = match.group(1)
        if relation and relation not in allowed_relations:
            return _fail(
                "allowed_relations",
                f"허용되지 않은 관계 타입이다: {relation}."
                f" 사용 가능: {', '.join(sorted(allowed_relations))}",
                passed,
            )
    passed.add("allowed_relations")

    # ── 6. LIMIT 강제 (없으면 주입, 초과면 축소) ───────────────────────
    normalized = stripped
    tail = _LIMIT_TAIL_RE.search(stripped)
    if tail is None:
        normalized = f"{stripped} LIMIT {_MAX_ROWS}"
    else:
        try:
            current = int(tail.group(1))
        except ValueError:
            normalized = _LIMIT_TAIL_RE.sub(f"LIMIT {_MAX_ROWS}", stripped)
        else:
            if current > _MAX_ROWS:
                normalized = _LIMIT_TAIL_RE.sub(f"LIMIT {_MAX_ROWS}", stripped)
    passed.add("limit_enforced")

    checks = tuple(CypherCheckResult(key=key, passed=True) for key in CYPHER_CHECK_KEYS)
    return CypherValidationResult(
        valid=True, normalized_cypher=normalized, reason=None, checks=checks
    )
