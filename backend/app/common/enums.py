from enum import StrEnum


class Judgement(StrEnum):
    IN_CONTROL = "IN_CONTROL"
    OOC = "OOC"
    OOS = "OOS"


class AgentRunStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ActionApprovalStatus(StrEnum):
    AUTO = "AUTO"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class SendStatus(StrEnum):
    WAITING = "WAITING"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class Decision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ChamberStatus(StrEnum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    ALARM = "ALARM"
    CRITICAL = "CRITICAL"


class ToolCallStatus(StrEnum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


class ChartType(StrEnum):
    TABLE = "table"
    BAR = "bar"
    LINE = "line"
    HISTOGRAM = "histogram"


class ActionCode(StrEnum):
    MONITOR = "MONITOR"
    NOTIFY = "NOTIFY"
    LOT_HOLD = "LOT_HOLD"
    EQP_HOLD = "EQP_HOLD"


class SendChannel(StrEnum):
    EMAIL = "EMAIL"
    MES = "MES"


class FaultCode(StrEnum):
    FOC = "FOC"
    RFM = "RFM"
    MFD = "MFD"
    TMD = "TMD"


class ActorType(StrEnum):
    SYSTEM = "SYSTEM"
    AGENT = "AGENT"
    HUMAN = "HUMAN"


# 조치에 딸린 값은 규칙으로 고정한다. decide_action 이 LLM 출력으로 채우지 않는다.
_ACTION_SEVERITY: dict[ActionCode, Severity] = {
    ActionCode.MONITOR: Severity.LOW,
    ActionCode.NOTIFY: Severity.MEDIUM,
    ActionCode.LOT_HOLD: Severity.MEDIUM,
    ActionCode.EQP_HOLD: Severity.HIGH,
}

_ACTION_SEND_CHANNEL: dict[ActionCode, SendChannel] = {
    ActionCode.MONITOR: SendChannel.EMAIL,
    ActionCode.NOTIFY: SendChannel.EMAIL,
    ActionCode.LOT_HOLD: SendChannel.MES,
    ActionCode.EQP_HOLD: SendChannel.MES,
}

# 승인 대상은 EQP_HOLD 뿐이다. 설정으로 넓히면 안전장치가 되지 못한다.
APPROVAL_REQUIRED_ACTIONS: frozenset[ActionCode] = frozenset({ActionCode.EQP_HOLD})


def resolve_severity(action_code: ActionCode) -> Severity:
    return _ACTION_SEVERITY[action_code]


def resolve_send_channel(action_code: ActionCode) -> SendChannel:
    return _ACTION_SEND_CHANNEL[action_code]


def requires_approval(action_code: ActionCode) -> bool:
    return action_code in APPROVAL_REQUIRED_ACTIONS
