const EVENT_LABELS = Object.freeze({
  DETECTION_COMPLETED: '이상 감지 완료',
  AGENT_RUN_STARTED: '에이전트 분석 시작',
  HYPOTHESIS_GENERATED: '원인 가설 생성',
  APPROVAL_REQUESTED: '조치 승인 요청',
  APPROVAL_DECIDED: '조치 승인 결정',
  ACTION_SENT: '조치 전달 완료',
  ACTION_SEND_FAILED: '조치 전달 실패',
  AGENT_RUN_COMPLETED: '에이전트 분석 완료',
  AGENT_RUN_FAILED: '에이전트 분석 실패',
})

const ENTITY_LABELS = Object.freeze({
  LOT_HIST: 'LOT 이력',
  AGENT_RUN: '에이전트 실행',
  APPROVAL: '승인',
  ACTION: '조치',
})

const ACTOR_LABELS = Object.freeze({
  SYSTEM: '시스템',
  AGENT: '에이전트',
  HUMAN: '사용자',
})

const VALUE_LABELS = Object.freeze({
  RUNNING: '실행 중',
  WAITING_APPROVAL: '승인 대기',
  COMPLETED: '완료',
  FAILED: '실패',
  AUTO: '자동 처리',
  PENDING: '승인 대기',
  APPROVED: '승인',
  REJECTED: '반려',
  EXPIRED: '만료',
  EMAIL: '이메일',
  MES_MOCK: 'MES 모의 연동',
  BLOCKED: '차단',
  WAITING: '대기',
  SENDING: '전송 중',
  SENT: '전송 완료',
  CANCELED: '취소',
  UNKNOWN: '확인 필요',
})

const labelOf = (labels, value, fallback = '미제공') => labels[value] ?? value ?? fallback

export const auditEventLabel = (value) => labelOf(EVENT_LABELS, value)
export const auditEntityLabel = (value) => labelOf(ENTITY_LABELS, value)
export const auditActorLabel = (value) => labelOf(ACTOR_LABELS, value)
export const auditValueLabel = (value) => labelOf(VALUE_LABELS, value)
