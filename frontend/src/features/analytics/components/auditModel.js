// 감사로그 화면 상수·순수 함수 — 컴포넌트 파일(fast refresh 제약) 밖으로 분리한 모듈
export const ALL = '전체'

// 기본 기간 = 오늘 포함 최근 30일 (V5-D-1.4). mock 시절 고정 날짜는 실데이터를 항상
// 기간 밖으로 밀어내 빈 화면을 만들었다 — 날짜는 로컬 기준 YYYY-MM-DD.
const isoDate = (d) => {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}
export const defaultAuditPeriod = (now = new Date()) => {
  const from = new Date(now)
  from.setDate(from.getDate() - 30)
  return { from: isoDate(from), to: isoDate(now) }
}
export const DEFAULT_AUDIT_FILTER = { ...defaultAuditPeriod(), type: ALL, actor: ALL, target: '' }

// ── 한글 표기 사전 — 표·필터·집계가 같은 사전을 본다 (코드값은 API 계약 그대로) ──
export const EVENT_LABEL = {
  DETECTION_COMPLETED: '탐지 완료',
  AGENT_RUN_STARTED: 'Agent 분석 시작',
  HYPOTHESIS_GENERATED: '원인 가설 생성',
  APPROVAL_REQUESTED: '승인 요청',
  APPROVAL_DECIDED: '승인 결정',
  ACTION_SENT: '조치 전송',
  ACTION_SEND_FAILED: '조치 전송 실패',
  AGENT_RUN_COMPLETED: 'Agent 분석 완료',
  AGENT_RUN_FAILED: 'Agent 분석 실패',
}
export const eventLabel = (type) => EVENT_LABEL[type] ?? type

export const ACTOR_LABEL = { AGENT: 'Agent', HUMAN: '사용자', USER: '사용자', SYSTEM: '시스템' }
export const actorLabel = (actor) => ACTOR_LABEL[actor] ?? actor

// 상세(detail)·before/after 의 키·상태값 표기
export const DETAIL_KEY_LABEL = {
  lot_id: '로트',
  wafer_no: '웨이퍼',
  chamber_id: '챔버',
  equipment_id: '설비',
  status: '상태',
  representative_alarm_id: '대표 알람',
  alarm_id: '알람',
  confidence: '신뢰도',
  predicted_fault_code: '예측 Fault',
  fault_code: 'Fault',
  action_id: '조치',
  action_code: '조치 코드',
  agent_run_id: '실행',
  approval_id: '승인',
  channel: '채널',
  decided_by: '결정자',
  reason: '사유',
  error: '오류',
}
export const DETAIL_VALUE_LABEL = {
  RUNNING: '실행 중',
  COMPLETED: '완료',
  FAILED: '실패',
  PENDING: '대기',
  APPROVED: '승인',
  REJECTED: '반려',
  SENT: '전송됨',
  SKIPPED: '생략',
}
export const detailKeyLabel = (k) => DETAIL_KEY_LABEL[k] ?? k
export const detailValueLabel = (v) => DETAIL_VALUE_LABEL[String(v)] ?? String(v)

// "lot_id LOT004, status RUNNING, chamber_id EQP04-PM2" → [[key, value], …]
// 형식이 아니면(자유 문장) 그대로 한 항목으로 돌려준다.
export function parseDetail(detail) {
  if (detail == null) return []
  if (typeof detail === 'object' && !Array.isArray(detail)) return Object.entries(detail).map(([k, v]) => [k, String(v)])
  const text = String(detail).trim()
  if (!text) return []
  const parts = text.split(/,\s*/)
  const pairs = parts.map((p) => {
    const i = p.indexOf(' ')
    return i > 0 ? [p.slice(0, i), p.slice(i + 1)] : [null, p]
  })
  // 절반 이상이 key value 형태일 때만 구조로 본다
  return pairs.filter(([k]) => k).length >= Math.ceil(pairs.length / 2) ? pairs : [[null, text]]
}

// 대상 열의 주 표시 — 알람 ID 가 있으면 그것, 없으면 entity_id
export function primaryTargetOf(e) {
  const pairs = parseDetail(e.detail)
  const alarm = pairs.find(([k]) => k === 'representative_alarm_id' || k === 'alarm_id')?.[1]
  return { primary: alarm ?? e.entity_id, secondary: alarm ? e.entity_id : null }
}

// ── 색 — 4톤 틴트 (앱 팔레트). 시작·탐지 navy / 가설·승인 blue / 완료·전송 green / 실패 red ──
export const EVENT_TONE = {
  DETECTION_COMPLETED: 'navy',
  AGENT_RUN_STARTED: 'navy',
  HYPOTHESIS_GENERATED: 'blue',
  APPROVAL_REQUESTED: 'blue',
  APPROVAL_DECIDED: 'blue',
  ACTION_SENT: 'green',
  AGENT_RUN_COMPLETED: 'green',
  ACTION_SEND_FAILED: 'red',
  AGENT_RUN_FAILED: 'red',
}
export const eventTone = (type) => EVENT_TONE[type] ?? 'gray'
export const TONE_BADGE = { navy: 't-navy', blue: 't-blue', green: 't-green', red: 't-red', gray: 't-gray' }
export const TONE_HEX = {
  navy: 'var(--color-navy)',
  blue: 'var(--color-blue)',
  green: 'var(--color-green)',
  red: 'var(--color-red)',
  gray: 'var(--color-g2)',
}
// (하위 호환) 이전 호출부용
export const eventHex = (type) => TONE_HEX[eventTone(type)]
