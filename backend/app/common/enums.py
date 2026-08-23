from enum import StrEnum


class AlarmSource(StrEnum):
    TRACE = "TRACE"
    SUMMARY = "SUMMARY"
    R03 = "R03"


class AlarmType(StrEnum):
    IN = "IN"
    OOC = "OOC"
    OOS = "OOS"


class ActionCode(StrEnum):
    MONITORING = "MONITORING"
    WARNING = "WARNING"
    EQP_HOLD = "EQP_HOLD"


class ActionLinkRole(StrEnum):
    """`agent_run_action.link_role`. migration CHECK와 exact하게 맞춘다.

    `002_agent_runtime_clean.sql`이 `CHECK (link_role IN ('CREATED', 'REUSED'))`로
    강제한다. 값을 늘리면 DTO는 통과하고 insert가 실패한다.
    """

    CREATED = "CREATED"
    REUSED = "REUSED"


class FaultHypothesis(StrEnum):
    FOC = "FOC"
    RFM = "RFM"
    MFD = "MFD"
    TMD = "TMD"
    OTH = "OTH"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ApprovalStatus(StrEnum):
    AUTO = "AUTO"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class DeliveryChannel(StrEnum):
    EMAIL = "EMAIL"
    MES_MOCK = "MES_MOCK"


class DeliveryStatus(StrEnum):
    BLOCKED = "BLOCKED"
    WAITING = "WAITING"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    UNKNOWN = "UNKNOWN"


class ThresholdValidationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"


class IncidentModelSignalStatus(StrEnum):
    READY = "READY"
    DISABLED = "DISABLED"
    UNAVAILABLE = "UNAVAILABLE"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Decision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ToolCallStatus(StrEnum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


class ChartType(StrEnum):
    TABLE = "table"
    BAR = "bar"
    LINE = "line"
    HISTOGRAM = "histogram"


class ActorType(StrEnum):
    SYSTEM = "SYSTEM"
    AGENT = "AGENT"
    HUMAN = "HUMAN"


# 조치 부가값은 LLM 출력이 아니라 이 규칙표에서만 파생한다.
_ACTION_SEVERITY: dict[ActionCode, Severity] = {
    ActionCode.MONITORING: Severity.LOW,
    ActionCode.WARNING: Severity.MEDIUM,
    ActionCode.EQP_HOLD: Severity.HIGH,
}

_ACTION_DELIVERY_CHANNELS: dict[ActionCode, tuple[DeliveryChannel, ...]] = {
    ActionCode.MONITORING: (),
    ActionCode.WARNING: (DeliveryChannel.EMAIL,),
    ActionCode.EQP_HOLD: (
        DeliveryChannel.EMAIL,
        DeliveryChannel.MES_MOCK,
    ),
}

# 사람 승인 대상은 EQP_HOLD뿐이다. 설정으로 우회할 수 없다.
APPROVAL_REQUIRED_ACTIONS: frozenset[ActionCode] = frozenset({ActionCode.EQP_HOLD})


def resolve_severity(action_code: ActionCode) -> Severity:
    return _ACTION_SEVERITY[action_code]


def resolve_delivery_channels(
    action_code: ActionCode,
) -> tuple[DeliveryChannel, ...]:
    """조치의 전체 delivery 계획을 반환한다.

    현재 호출 가능한 채널은 C Service가 approval·delivery 상태로 다시 제한한다.
    특히 EQP_HOLD의 MES_MOCK은 승인 전 실행 대상이 아니다.
    """

    return _ACTION_DELIVERY_CHANNELS[action_code]


def requires_approval(action_code: ActionCode) -> bool:
    return action_code in APPROVAL_REQUIRED_ACTIONS


# ---------------------------------------------------------------------
# 공개 계약 전용 Enum
#
# 내부 Runtime 값과 **의도적으로 다르다.** 같은 이름을 재사용하면 내부 상태가
# 공개 API로 새거나, 공개 입력이 내부 저장 값으로 그대로 흘러간다.
# 변환은 `boundary_adapters`의 explicit dictionary만 담당한다.
# ---------------------------------------------------------------------
class PublicApprovalDecision(StrEnum):
    """공개 승인 요청 값. 내부 `Decision(APPROVE|REJECT)`와 다르다.

    API 명세 v3: "public 요청은 `APPROVED|REJECTED`만 받는다."
    """

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PublicApprovalStatus(StrEnum):
    """공개 승인 목록 상태. 내부 `ApprovalStatus` 5종의 부분집합이다.

    내부 전용 `AUTO`·`EXPIRED`는 공개하지 않는다. 조회에서 제외할지 다르게
    표시할지는 `V5-C-5.1` Repository·API가 정한다.
    """

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PublicDeliveryChannel(StrEnum):
    """공개 delivery channel. 내부 `MES_MOCK`을 `MES`로 projection한다.

    suffix 제거 같은 암묵 변환을 쓰지 않는다 — channel이 늘면 조용히 깨진다.
    """

    EMAIL = "EMAIL"
    MES = "MES"
