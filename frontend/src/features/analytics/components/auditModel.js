// 감사로그 화면 상수·순수 함수 — 컴포넌트 파일(fast refresh 제약) 밖으로 분리한 모듈
export const ALL = '전체'
export const DEFAULT_AUDIT_FILTER = { from: '2026-06-01', to: '2026-06-04', type: ALL, actor: ALL, target: '' }

// 이벤트 유형 색 — 유형 뱃지·집계 바 공통
export const EVENT_HEX = {
  DETECTION_COMPLETED: '#2563eb',
  AGENT_RUN_STARTED: '#1c3150',
  HYPOTHESIS_GENERATED: '#0ea5e9',
  APPROVAL_REQUESTED: '#f59e0b',
  APPROVAL_DECIDED: '#7c3aed',
  ACTION_SENT: '#16a34a',
  ACTION_SEND_FAILED: '#dc2626',
  AGENT_RUN_COMPLETED: '#15803d',
  AGENT_RUN_FAILED: '#dc2626',
}
const FALLBACK_HEX = '#94a3b8'
export const eventHex = (type) => EVENT_HEX[type] ?? FALLBACK_HEX
