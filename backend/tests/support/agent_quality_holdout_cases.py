"""Authored numeric holdouts of eight development patterns, not final data.

These inputs contain no source-data rows, CF oracle, provider response or fault
answer. They are structural holdouts of the existing toy patterns, not an
independent sample of production failures. Evaluator metadata is never passed to
the model. The original cases and document corpus remain unchanged.
"""

from dataclasses import asdict, dataclass, replace

from app.common.tool_contracts import DocumentHit, DocumentSearchToolResult
from tests.support.agent_quality_development_cases import (
    CASES as BASE_CASES,
)
from tests.support.agent_quality_development_cases import (
    DOCUMENTS as BASE_DOCUMENTS,
)
from tests.support.agent_quality_development_cases import (
    MODEL,
    DevelopmentCase,
    SyntheticInvestigationTools,
    _document_topic,
)


@dataclass(frozen=True)
class HoldoutCase(DevelopmentCase):
    """Fixture corpus marker; not a model input or expected outcome."""


_BASE_HOLDOUT_CASES = tuple(HoldoutCase(**asdict(case)) for case in BASE_CASES)

CASES = (
    replace(
        _BASE_HOLDOUT_CASES[0],
        case_id="HOLD-01",
        title="정상 현재값과 정상 이력의 수치 변형",
        acceptance=(
            "현재 P1=4 및 과거 정상 관측을 저장 알람과 구분한다",
            "현재 정상과 형제 정상으로 물리적 고장이나 전체 정상을 단정하지 않는다",
        ),
        current_means=(4.0, 4.0),
        prior_means=(4.0, 4.0, 4.0),
    ),
    replace(
        _BASE_HOLDOUT_CASES[1],
        case_id="HOLD-02",
        title="단일 현재 하한 이탈",
        acceptance=(
            "실제 하한 이탈값을 인용하고 상한 이탈로 뒤집지 않는다",
            "계측 PASS로 FDC 하한 이탈을 취소하지 않는다",
        ),
        current_means=(-2.0,),
    ),
    replace(
        _BASE_HOLDOUT_CASES[2],
        case_id="HOLD-03",
        title="상류 하한 이탈과 현재 정상",
        acceptance=(
            "상류 P1=-2와 현재 P1=5를 실제 공정 범위와 함께 구분한다",
            "상류 계측 부재와 현재 계측을 구분하고 전파를 단정하지 않는다",
        ),
        current_means=(5.0,),
        upstream_mean=-2.0,
    ),
    replace(
        _BASE_HOLDOUT_CASES[3],
        case_id="HOLD-04",
        title="현재 두 wafer 하한 이탈과 형제 정상",
        acceptance=(
            "두 현재 wafer의 하한 이탈과 동일 설비 형제의 현재 정상을 대조한다",
            "형제 현재 관측과 형제 과거 표본 부재를 구분한다",
        ),
        current_means=(-2.0, -2.0),
    ),
    replace(
        _BASE_HOLDOUT_CASES[4],
        case_id="HOLD-05",
        title="과거 감소 추세와 현재 하한 이탈",
        acceptance=(
            "과거값을 시간순 4,3,2에서 현재 -2로 비교하고 감소를 설명한다",
            "관측된 감소 추세와 물리적 원인 확정을 구분한다",
        ),
        current_means=(-2.0,),
        prior_means=(2.0, 3.0, 4.0),
    ),
    replace(
        _BASE_HOLDOUT_CASES[5],
        case_id="HOLD-06",
        title="하한 이탈과 문서 일시 실패 뒤 회복",
        acceptance=(
            "문서 TIMEOUT을 부재 또는 성공 근거로 바꾸지 않는다",
            "회복된 문서 또는 다른 근거와 하한 관측을 구분해 설명한다",
        ),
        current_means=(-2.0,),
    ),
    replace(
        _BASE_HOLDOUT_CASES[6],
        case_id="HOLD-07",
        title="두 번째 wafer에서만 하한 이탈",
        acceptance=(
            "첫 wafer의 P1=5 정상만으로 전체 정상이라고 일반화하지 않는다",
            "두 번째 wafer P1=-2의 하한 이탈과 표본 차이를 인용한다",
        ),
        current_means=(5.0, -2.0),
    ),
    replace(
        _BASE_HOLDOUT_CASES[7],
        case_id="HOLD-08",
        title="하한 이탈 조사에서 같은 문서 반복 반환",
        acceptance=(
            "다른 질의로 반환된 동일 chunk를 신규 관측으로 세지 않는다",
            "하한 관측과 추가 정보 가치를 판단하며 예산 소진을 목표로 하지 않는다",
        ),
        current_means=(-2.0,),
    ),
)

HOLDOUT_CASES = CASES

# All holdouts share this authored corpus. Selection depends on the query, never
# case metadata, expected scope or a desired fault answer.
DOCUMENTS = {
    **BASE_DOCUMENTS,
    "direction": (
        "P1 하한 이탈 점검",
        "P1 하한 이탈 시 설정값과 센서 보정 이력, wafer별 편차를 점검한다. "
        "현재 수치 이탈만으로 장비 고장 종류를 확정할 수 없다.",
    ),
    "history": (
        "P1 추세 점검",
        "과거 LOT 집계는 시간순으로 현재값과 비교한다. "
        "지속 증가·감소와 갑작스런 변화는 구분하되 "
        "보정·정비 기록 확인 전 원인을 확정하지 않는다.",
    ),
}


class SyntheticHoldoutInvestigationTools(SyntheticInvestigationTools):
    """Same read DTO/ledger contracts with a separate lower-direction corpus."""

    def document_search(self, run_id, request):
        def read():
            attempts = sum(
                row.tool_name == "search_documents" for row in self.history(run_id)
            )
            if self.case.document_timeout_once and attempts == 1:
                return DocumentSearchToolResult(
                    ok=False, reason="TIMEOUT: temporary document read"
                )
            if request.model_code not in {None, MODEL}:
                return DocumentSearchToolResult(ok=True, hits=[])
            topic = _document_topic(request.query)
            if topic == "general" and any(
                term in request.query.lower() for term in ("하한", "lower", "below")
            ):
                topic = "direction"
            if self.case.repeated_document:
                topic = "general"
            title, content = DOCUMENTS[topic]
            return DocumentSearchToolResult(
                ok=True,
                hits=[
                    DocumentHit(
                        chunk_id=f"DOC-P1-{topic.upper()}",
                        document_id="DOC-P1",
                        title=title,
                        section="점검",
                        score=0.8,
                        content=content,
                        model_code=MODEL,
                    )
                ],
            )

        return self._call(run_id, "search_documents", "documents", request, read)


def tools_factory_for_case(
    case: DevelopmentCase,
) -> type[SyntheticInvestigationTools]:
    """Choose authored fixture data only; never expose metadata to the model."""
    if isinstance(case, HoldoutCase):
        return SyntheticHoldoutInvestigationTools
    if isinstance(case, DevelopmentCase):
        return SyntheticInvestigationTools
    raise ValueError("UNKNOWN_SYNTHETIC_DEVELOPMENT_CASE")
