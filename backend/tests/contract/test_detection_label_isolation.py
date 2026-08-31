"""V5-A-2.3 합성 라벨 격리 계약 테스트.

시스템설계서 v2.1 2.6, WBS v5 V5-A-2.3 완료 기준: "fault_code를 평가
loader에서만 읽고 Runtime repository 타입과 분리한다. 모델 feature·
threshold·Tool·API에 사용하지 않음을 allowlist·query·payload 테스트로
고정한다."

- "query" 테스트(Runtime repository 함수가 fault_code를 SELECT하지 않는다)는
  이미 `tests/unit/test_detection_model.py::
  test_repository_fetch_functions_never_touch_labels`가 담당한다(V5-A-2.3에서
  `fetch_wafer_lot_context`·`fetch_wafer_parameter_rows` 두 개를 새로 추가해
  범위를 넓혔다). 이 파일에서 중복 구현하지 않는다.
- 이 파일은 나머지 둘을 담당한다: "allowlist"(evaluation_loader를 아무도
  잘못된 곳에서 import하지 않는다)와 "payload"(A의 API·Tool 응답 스키마
  어디에도 fault_code·라벨 필드가 없다).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from pydantic import BaseModel

from app.common import tool_contracts
from app.detection import evaluation_loader, public_schemas, schemas
from app.evaluation import predictions_repository

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

#: evaluation_loader.py를 import해도 되는 유일한 곳 — 지금은 아무 데도 없다.
#: `app/detection/evaluation.py`(V5-A-2.4)조차 직접 import하지 않는다: 순서
#: 강제를 위해 함수 주입(dependency injection)으로 label 조회 함수를
#: 받기 때문이다(`evaluation_loader.py` 모듈 docstring 참고). 즉 이 loader를
#: 실제로 import하는 곳은 DB에 연결하는 실행 스크립트
#: (`scripts/evaluate_detection_holdout.py`)뿐이며, 그 스크립트는 이
#: 저장소의 `app` 패키지 밖이라 이 allowlist의 대상이 아니다.
_EVALUATION_LOADER_IMPORT_ALLOWLIST: frozenset[str] = frozenset()


def _imports_evaluation_loader(path: Path) -> bool:
    """`path`가 `evaluation_loader` 모듈을 실제로 import하는지 AST로
    판정한다(코드 리뷰 필수 4).

    `ast.Import`/`ast.ImportFrom` 노드만 본다 — 주석·docstring의 문자열은
    AST에서 애초에 Import 노드로 나타나지 않으므로, "evaluation_loader"라는
    단어가 산문(docstring)에 등장해도 이 함수는 그것을 보지 않는다. 예전
    substring 검사(`"evaluation_loader" in text`)는 그 산문까지 걸려서
    `evaluation.py`가 이 모듈을 이름으로 설명하지 못하게 만들었다 — 계약
    테스트가 문서의 표현력을 깎는 상태였다.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "evaluation_loader" in alias.name.split("."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module_parts = (node.module or "").split(".")
            if "evaluation_loader" in module_parts:
                return True
            if any(alias.name == "evaluation_loader" for alias in node.names):
                return True
    return False


def test_nothing_in_app_imports_evaluation_loader_except_the_allowlist() -> None:
    """`evaluation_loader`를 실제로 import하는 `app/` 파일이 allowlist와
    정확히 일치하는지 확인한다. 지금은 allowlist가 비어 있으므로 사실상
    "아무도 import하지 않는다"를 고정한다 — model.py·service.py·tools.py·
    router.py 같은 Runtime 경로가 실수로라도 이 loader를 끌어오면 이 테스트가
    즉시 깨진다.

    import 문(AST `Import`/`ImportFrom` 노드)만 검사한다(코드 리뷰 필수 4) —
    `evaluation.py`의 docstring처럼 이 모듈을 이름으로 설명하는 산문은 이
    테스트를 건드리지 않는다.
    """

    loader_path = Path(evaluation_loader.__file__).resolve()
    referencing: set[str] = set()
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or path.resolve() == loader_path:
            continue
        if _imports_evaluation_loader(path):
            referencing.add(str(path.relative_to(APP_ROOT)).replace("\\", "/"))

    assert referencing == _EVALUATION_LOADER_IMPORT_ALLOWLIST, (
        "evaluation_loader를 import하는 파일이 allowlist와 다릅니다: "
        f"{sorted(referencing)}"
    )


def test_evaluation_loader_actually_selects_fault_code() -> None:
    """양성 대조군(positive control) — loader가 실제로 fault_code를 읽지
    *못하게* 실수로 고쳐버리면(예: column을 빼먹으면) 이 테스트가 깨진다.
    "아무도 못 읽는다"만 검사하면 "원래 읽어야 할 곳도 못 읽는" 퇴행을
    놓친다.
    """

    source = inspect.getsource(evaluation_loader.fetch_synthetic_fault_labels)
    assert "fault_code" in source
    incident_source = inspect.getsource(evaluation_loader.fetch_incident_fault_labels)
    assert "fault_code" in incident_source
    assert "lot_id" in incident_source
    assert "chamber_id" in incident_source


def test_fault_evaluation_runtime_query_never_reads_ground_truth() -> None:
    """prediction 연결은 LEFT JOIN을 유지하고 raw label column을 읽지 않는다."""

    sql = str(predictions_repository.PREDICTIONS_SQL).lower()
    assert "left join agent_prediction" in sql
    assert "agent_prediction_review" not in sql
    assert "evaluation" not in sql
    assert "lot_history" not in sql
    # ``predicted_fault_code``는 허용하지만 독립 token ``fault_code``는 금지한다.
    assert " fault_code" not in sql
    assert ".fault_code" not in sql


#: `fault` alias를 가질 수 있는 유일한 공개 DTO다. API v3 §2.7이 고정한 참고
#: React 호환 projection이며, 값은 Runtime Agent 예측(`agent_prediction`)의
#: nullable 복사본이다 — 합성 GT(`lot_history.fault_code`)나 parameter→Fault
#: 고정표에서 만드는 것은 §2.7이 명시적으로 금지한다. 이 집합 **밖의** 모든
#: Detection DTO는 라벨 성격 필드를 가질 수 없다.
_COMPAT_ALIAS_MODELS = frozenset({"AlarmItem", "TracePoint", "ParameterItem"})


def _pydantic_models_in(module: object) -> list[type[BaseModel]]:
    return [
        obj
        for _, obj in vars(module).items()
        if isinstance(obj, type)
        and issubclass(obj, BaseModel)
        and obj.__module__ == module.__name__  # type: ignore[attr-defined]
    ]


def test_detection_api_schemas_have_no_label_field() -> None:
    """Detection 계산·평가 내부 DTO에는 prediction·label이 역류하지 않는다.

    API v3 공개 boundary의 ``predicted_fault_code``와 deprecated ``fault``는 Agent
    Runtime prediction의 nullable projection이며 합성 ground truth가 아니다.
    """

    checked = 0
    for model in _pydantic_models_in(schemas) + _pydantic_models_in(public_schemas):
        if model.__name__ in _COMPAT_ALIAS_MODELS:
            continue
        checked += 1
        for field_name in model.model_fields:
            lowered = field_name.lower()
            assert (
                "fault" not in lowered and "label" not in lowered
            ), f"{model.__qualname__}.{field_name}가 라벨 성격의 필드입니다"

    assert checked > 0

    public_alarm_fields = set(public_schemas.AlarmItem.model_fields)
    assert {"predicted_fault_code", "fault"} <= public_alarm_fields
    assert "fault_code" not in public_alarm_fields
    assert not any("label" in field.lower() for field in public_alarm_fields)


def test_fdc_summary_tool_result_has_no_label_field() -> None:
    """get_fdc_summary(V5-A-3.2-1)의 반환 계약에도 라벨 필드가 없는지
    확인한다 — `FdcSummaryToolResult`는 `app/detection/schemas.py`가 아니라
    `app/common/tool_contracts.py`에 정의돼 있어 위 테스트가 못 본다.
    """

    for field_name in tool_contracts.FdcSummaryToolResult.model_fields:
        lowered = field_name.lower()
        assert (
            "fault" not in lowered and "label" not in lowered
        ), f"FdcSummaryToolResult.{field_name}가 라벨 성격의 필드입니다"
