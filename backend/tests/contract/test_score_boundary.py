"""V5-A-2.2 score 경계 고정 계약 테스트.

시스템설계서 v2.1 4.4: "anomaly score는 규칙 alarm을 대체하지 않고 wafer 이상
정도를 설명하는 보조 근거다 ... Agent는 score를 근거 문장에 인용할 수 있지만
조치 결정 근거와 구분한다." 완료 기준(WBS v5 V5-A-2.2): "score가 조치·incident·
승인 게이트에 전달되지 않음을 계약 테스트로 고정한다. score 없이도 규칙 처리가
동일하다."

## 이 파일이 새로 잠그는 것과, 이미 잠겨 있던 것

C 쪽(`app/agent/`)은 이미 여러 모듈에서 개별적으로 "anomaly_score" 금지 토큰
검사를 하고 있었다(`test_agent_run_guard.py::test_no_label_or_score_names`,
`test_agent_incident.py::test_no_label_or_reference_names_reach_the_query`,
`test_agent_routing.py::test_no_label_or_reference_names_leak`) — 각각 승인
게이트(run_guard)·incident 해석·routing 개별 모듈 단위 계약이다. 이 파일은
그 셋을 대신하지 않는다(중복 구현하지 않는다). 대신 A가 실제로 소유하는 것 —
"score가 어디서 만들어지고 어디까지 흘러가는지"의 저장소 전체 경계 — 를 아래
다섯 가지로 고정한다.

1. score(`AnomalySignal`)를 참조하는 파일이 저장소 전체에서 정확히 이
   allowlist뿐이라는 것(신규 유출 경로가 생기면 이 테스트가 가장 먼저 깨진다).
2. `app/agent/state.py`에서 `AnomalySignal`이 쓰이는 자리가 전부 "evidence"라는
   이름을 달고 있다는 것.
3. A 자신의 규칙 처리 함수(`rules.py`·`service.py`의 verify_*·R03 파생·적재)가
   애초에 score를 인자로 받을 수 없는 시그니처라는 것 — "score 없이도 규칙
   처리가 동일하다"를 "실행해서 비교"가 아니라 "애초에 입력받을 수 없다"는
   더 강한 방식으로 고정한다.
4. `to_anomaly_signal`이 만드는 `AnomalySignal`은 항상 `action_threshold`·
   `threshold_version`이 비어 있다는 것(표시용 근거와 조치용 threshold가
   스키마 필드 차원에서도 분리돼 있다는 설계 4.4의 마지막 문장을 실제 값으로
   확인한다).
5. `MODEL_SIGNAL_ENABLED`(config.py: "실제 gate 사용은 C 정책 후속이다")가
   아직 어떤 게이트 코드에서도 참조되지 않는다는 것 — 참조되기 시작하면 그건
   C가 게이트를 실제로 연결했다는 신호이므로, 이 테스트가 깨져서 팀이 그
   변경을 의식적으로 검토하게 만든다.

미래에 `app/agent/decision.py`(ActionPolicy, V5-C 영역)가 채워지면
`test_future_action_policy_has_no_score_parameter`가 자동으로 실제 검사로
전환된다 — 지금처럼 빈 파일인 동안은 skip된다(`app/detection/model.py` 모듈
docstring의 "[V5-A-2.2 예고]" 참고).
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

from app.detection import model as anomaly_model
from app.detection import rules, service

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

_SCORE_MARKERS = ("AnomalySignal", "anomaly_score")

# score(AnomalySignal)가 등장해도 되는 유일한 파일들. 새로 만들면 여기부터
# 검토한다 — 늘리는 것 자체가 잘못은 아니지만, "왜 이 파일이 score를 알아야
# 하는가"에 답할 수 있어야 한다.
#
# `common/config.py`·`detection/model_artifact.py`는 예전(substring 기반)
# 검사에서는 이 목록에 있었다 — 둘 다 실제 코드가 아니라 주석·docstring에서만
# "AnomalySignal"/"anomaly_score"를 언급했기 때문이다(config.py: "환경변수
# threshold는 model manifest의 AnomalySignal로만 주입한다"는 주석 한 줄;
# model_artifact.py: `train_anomaly_score_model.py` 스크립트 이름을 설명하며
# "anomaly_score"라는 글자가 우연히 포함됐을 뿐). AST 식별자 검사(아래
# `_score_marker_references`)로 바꾸면 둘 다 실제로는 참조하지 않는다는 게
# 드러나므로 이 목록에서 뺐다(코드 리뷰 필수 4).
_SCORE_REFERENCE_ALLOWLIST = frozenset(
    {
        # AnomalySignal 정의 자체.
        "common/tool_contracts.py",
        # score를 만드는 쪽(A 소유) — model.py가 계산·변환, service.py의
        # FdcSummaryService가 조립, schemas.py의 TraceCatalogResponse.anomaly가
        # 화면 adapter다.
        "detection/model.py",
        "detection/service.py",
        "detection/schemas.py",
        # "evidence" 필드로만 받는 쪽(2번 테스트가 그 필드 이름 자체를 확인한다).
        "agent/state.py",
    }
)


def _iter_app_py_files() -> list[Path]:
    return [
        path
        for path in sorted(APP_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def _score_marker_references(path: Path) -> set[str]:
    """`path`에서 `_SCORE_MARKERS`가 실제 코드 식별자로 등장하는지 AST로
    판정한다(코드 리뷰 필수 4).

    import 대상(module 경로·이름), 실제로 쓰이는 이름(`Name`)·속성 접근
    (`Attribute.attr`)·정의(`ClassDef`/`FunctionDef` 이름)·인자 이름만
    identifier로 인정한다. 문자열 리터럴(docstring 포함)은 `ast.Constant`로만
    나타나고 위 노드 종류가 전혀 아니므로 자동으로 제외된다 — "코드가 실제로
    이 이름을 참조하는지"와 "산문이 이 이름을 언급하는지"가 AST 레벨에서
    구조적으로 분리된다. 예전 substring 검사(`marker in text`)는 이 둘을
    구분하지 못해 `config.py`가 주석 한 줄 때문에 allowlist에 들어가 있었다.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.update(
                    part for part in alias.name.split(".") if part in _SCORE_MARKERS
                )
                if alias.asname in _SCORE_MARKERS:
                    found.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _SCORE_MARKERS:
                    found.add(alias.name)
                if alias.asname in _SCORE_MARKERS:
                    found.add(alias.asname)
        elif isinstance(node, ast.Name):
            if node.id in _SCORE_MARKERS:
                found.add(node.id)
        elif isinstance(node, ast.Attribute):
            if node.attr in _SCORE_MARKERS:
                found.add(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if node.name in _SCORE_MARKERS:
                found.add(node.name)
        elif isinstance(node, ast.arg):
            if node.arg in _SCORE_MARKERS:
                found.add(node.arg)
    return found


def test_score_reference_allowlist_is_exhaustive() -> None:
    """저장소 전체에서 AnomalySignal·anomaly_score를 참조하는 파일이
    allowlist와 정확히 일치하는지 확인한다 — 늘어나도, 줄어들어도 이 테스트가
    깨진다(허용 목록이 실제와 항상 같은 뜻을 유지하도록).

    "참조"는 AST 식별자 등장(`_score_marker_references`)만 센다(코드 리뷰
    필수 4) — 주석·docstring에서 이 이름을 언급하는 것만으로는 이 테스트가
    반응하지 않는다.
    """

    referencing: set[str] = set()
    for path in _iter_app_py_files():
        if _score_marker_references(path):
            referencing.add(str(path.relative_to(APP_ROOT)).replace("\\", "/"))

    unexpected = referencing - _SCORE_REFERENCE_ALLOWLIST
    assert not unexpected, (
        "score 참조가 새 파일에 등장했습니다(allowlist 갱신 필요 여부를 먼저 "
        f"검토하세요): {sorted(unexpected)}"
    )

    missing = _SCORE_REFERENCE_ALLOWLIST - referencing
    assert not missing, (
        f"allowlist에는 있는데 실제로는 참조하지 않는 파일입니다(목록을 좁히세요): "
        f"{sorted(missing)}"
    )


def test_agent_state_evidence_fields_are_named_as_evidence() -> None:
    """`app/agent/state.py`에서 `AnomalySignal`이 실제로 쓰이는 자리(주석·
    import 목록이 아니라 타입 annotation·대입)가 전부 "evidence"라는 이름을
    달고 있는지 확인한다 — 조치 결정에 쓰일 법한 이름으로 바뀌면 즉시 깨진다.
    """

    text = (APP_ROOT / "agent/state.py").read_text(encoding="utf-8")
    checked_lines = 0
    for line in text.splitlines():
        if not any(marker in line for marker in _SCORE_MARKERS):
            continue
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped in ("AnomalySignal", "AnomalySignal,"):
            continue  # import 목록의 타입 이름 나열일 뿐, 사용 자리가 아니다.
        if ":" not in line and "=" not in line:
            continue  # annotation도 대입도 아닌 자리(예: import 줄바꿈)는 건너뛴다.
        checked_lines += 1
        assert "evidence" in line.lower(), (
            f"agent/state.py에서 evidence가 아닌 자리에 score가 등장했습니다: "
            f"{line!r}"
        )

    assert checked_lines > 0, (
        "agent/state.py에서 AnomalySignal이 실제로 쓰이는 자리를 찾지 못했습니다 "
        "— 이 테스트의 탐색 로직을 갱신하세요"
    )


@pytest.mark.parametrize(
    "fn",
    [
        rules.judge_summary_alarm,
        rules.build_summary_alarm_flags,
        rules.compute_summary_control_limits,
        rules.derive_r03_events,
        rules.build_r03_alarm_record,
        service.verify_summary_recalculation,
        service.verify_evaluation_recalculation,
        service.verify_trace_alarm_reproduction,
        service.verify_summary_alarm_reproduction,
        service.verify_alarm_reproduction,
        service.derive_r03_events,
        service.derive_r03_alarm_records,
        service.persist_r03_alarms,
        service.verify_incident_aggregation,
    ],
)
def test_rule_processing_functions_cannot_accept_a_score(fn: object) -> None:
    """규칙 처리·incident 집계 함수는 애초에 score를 받을 자리가 없다 —
    "score 없이도 규칙 처리가 동일하다"는 완료 기준을, "동일한지 실행해서
    비교"하는 대신 "애초에 입력받을 수 없다"는 더 강한 방식으로 고정한다
    (입력받을 수 없으면 항상 결과가 같을 수밖에 없다).
    """

    parameters = inspect.signature(fn).parameters  # type: ignore[arg-type]
    for name in parameters:
        lowered = name.lower()
        assert "score" not in lowered and "anomaly" not in lowered, (
            f"{fn!r}가 score/anomaly 성격의 인자를 받습니다: {name}"  # type: ignore[str-format]
        )


def test_to_anomaly_signal_never_fills_action_threshold() -> None:
    """설계서 4.4 마지막 문장(action_threshold를 채우는 것은 이 프로젝트 범위
    밖)을 실제 값으로 고정한다 — display 근거와 조치용 threshold가 스키마
    필드 차원에서도 항상 분리돼 있어야 한다.
    """

    manifest = anomaly_model.ModelManifest(
        model_version="test-v0",
        score_method="isolation_forest_path_length",
        random_seed=1,
        feature_names=("P1__step1__mean_dist",),
        normalizer=anomaly_model.Normalizer(mean=(0.0,), std=(1.0,)),
        scaling=anomaly_model.ScoreScaling(raw_min=0.0, raw_max=1.0),
        display_threshold=0.5,
        n_estimators=1,
        contamination="auto",
        train_lot_count=1,
        test_lot_count=1,
        train_wafer_count=1,
        expected_point_counts=(("P1", 1, 1),),
        sklearn_version="0",
        numpy_version="0",
    )

    signal = anomaly_model.to_anomaly_signal(0.7, manifest)

    assert signal.action_threshold is None
    assert signal.threshold_version is None


def test_model_signal_enabled_flag_is_not_yet_wired_to_any_gate() -> None:
    """`MODEL_SIGNAL_ENABLED`(config.py: "실제 gate 사용은 C 정책 후속이다")가
    아직 어떤 모듈에서도 읽히지 않는지 확인한다. 이 값을 실제로 읽는 코드가
    생기면 — 게이트가 score에 연결되기 시작했다는 뜻이므로 — 이 테스트가
    깨져 팀이 V5-A-2.2 경계 계약을 다시 검토하게 만든다(정의 자체를 담은
    config.py는 검사 대상에서 제외한다).
    """

    exceptions = {"common/config.py"}
    for path in _iter_app_py_files():
        relative = str(path.relative_to(APP_ROOT)).replace("\\", "/")
        if relative in exceptions:
            continue
        text = path.read_text(encoding="utf-8")
        assert "MODEL_SIGNAL_ENABLED" not in text, (
            f"{relative}가 MODEL_SIGNAL_ENABLED를 참조합니다 — 게이트 연결이 "
            "시작됐다면 V5-A-2.2 경계 계약을 함께 갱신해야 합니다"
        )


def test_future_action_policy_has_no_score_parameter() -> None:
    """`app/agent/decision.py`(ActionPolicy, V5-C 영역)는 이 저장소에 아직
    구현되지 않았다 — `app/detection/model.py` 모듈 docstring의 "[V5-A-2.2
    예고]" 참고. 비어 있는 동안은 검사할 대상이 없으므로 skip하고, 채워지는
    즉시 그 public 함수·클래스(의 `decide`)의 시그니처에 score 인자가 없는지를
    자동으로 검사하기 시작한다(이 테스트 파일을 다시 손대지 않아도 된다).
    """

    module = importlib.import_module("app.agent.decision")
    source_path = Path(module.__file__)  # type: ignore[arg-type]
    source = source_path.read_text(encoding="utf-8").strip()

    if not source:
        pytest.skip(
            "app/agent/decision.py가 아직 비어 있습니다(ActionPolicy 미구현, "
            "V5-C 영역) — 구현되는 즉시 이 테스트가 실제 검사로 전환됩니다"
        )

    checked = 0
    for name, obj in vars(module).items():
        if name.startswith("_") or inspect.getmodule(obj) is not module:
            continue
        target = obj
        if inspect.isclass(obj):
            target = getattr(obj, "decide", obj)
        if not callable(target):
            continue
        checked += 1
        for param_name in inspect.signature(target).parameters:
            lowered = param_name.lower()
            assert "score" not in lowered and "anomaly" not in lowered, (
                f"{module.__name__}.{name}가 score/anomaly 인자를 받습니다: "
                f"{param_name}"
            )

    assert checked > 0, (
        "app/agent/decision.py에 코드가 생겼지만 검사할 public 함수/클래스를 "
        "찾지 못했습니다 — 이 테스트의 탐색 로직을 갱신하세요"
    )
