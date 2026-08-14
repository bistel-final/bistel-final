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
