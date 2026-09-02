// 감사로그 화면 상수·순수 함수 — 컴포넌트 파일(fast refresh 제약) 밖으로 분리한 모듈
import { alarmDisplayLabel } from '../../agent/components/agentModel.js'

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

// ── 한글 표기 사전 — 표·필터·집계·드로어가 같은 사전을 본다 (코드값은 API 계약 그대로) ──
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

export const ENTITY_LABEL = {
  AGENT_RUN: 'Agent 실행',
  APPROVAL: '승인 요청',
  ACTION: '조치',
  ALARM: '알람',
  DETECTION: '탐지',
}
export const entityLabel = (t) => ENTITY_LABEL[t] ?? t

// 상세(detail)·before/after 의 키·상태값 표기
export const DETAIL_KEY_LABEL = {
  lot_id: '로트',
  wafer_no: '웨이퍼',
  chamber_id: '챔버',
  equipment_id: '설비',
  status: '상태',
  representative_alarm_id: '대표 알람',
  representative_alarm: '대표 알람',
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

// 값이 식별자(RUN-…, ACT-…, TAL-…, LOT004, EQP04-PM2)처럼 보이면 mono 로, 아니면 sans 로 —
// mono 는 "기계가 만든 ID" 신호에만 쓴다.
export const isIdLike = (v) => /^[A-Z]{2,}[-:]|^[A-Z]{3,}\d/.test(String(v))

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

// 대상 열 — Agent 화면과 같은 정규화 표기(alarmDisplayLabel). 실행 hash 는 보조 줄로 내린다.
// 알람·로트·챔버는 AGENT_RUN_STARTED 의 after 에만 실려 오므로, 같은 페이지의 실행 key 로 묶어
// 가설·실패·승인 요청 행에도 그 문맥을 물려준다 (buildRunContext). 묶이지 않는 행은 entity_id 그대로.
export const ALARM_KEYS = new Set(['representative_alarm', 'representative_alarm_id', 'alarm_id'])

const alarmContextOf = (e) => {
  const pairs = [...parseDetail(e.detail), ...parseDetail(e.after), ...parseDetail(e.before)]
  const get = (k) => pairs.find(([key]) => key === k)?.[1]
  const raw = pairs.find(([k]) => ALARM_KEYS.has(k))?.[1]
  if (!raw) return null
  const [source, alarmId] = raw.includes(':') ? raw.split(':', 2) : [null, raw]
  return { source, alarmId, chamberId: get('chamber_id'), lotId: get('lot_id'), waferNo: get('wafer_no') }
}

// 실행 key → 알람 문맥.
// 1차: GET /agent/runs 응답(alarm_source·alarm_id·chamber_id·lot_id) — Agent 화면과 같은 원천이라 표기가 일치한다.
// 2차: 같은 페이지 감사 이벤트의 after(representative_alarm) — runs 조회가 실패하거나 없는 실행의 보결.
export function runContextFromRuns(runs = []) {
  const ctx = new Map()
  for (const r of runs) {
    if (!r?.agent_run_id) continue
    ctx.set(r.agent_run_id, {
      source: r.alarm_source ?? r.representative_alarm_source ?? null,
      alarmId: r.alarm_id ?? r.representative_alarm_id ?? null,
      chamberId: r.chamber_id ?? r.incident?.chamber_id ?? null,
      lotId: r.lot_id ?? r.incident?.lot_id ?? null,
      waferNo: r.wafer_no ?? null,
    })
  }
  return ctx
}

export function buildRunContext(items, base = new Map()) {
  const ctx = new Map(base)
  for (const e of items) {
    const key = runKeyOf(e)
    if (!key || ctx.has(key)) continue
    const c = alarmContextOf(e)
    if (c) ctx.set(key, c)
  }
  return ctx
}

export function primaryTargetOf(e, runContext = new Map()) {
  const key = runKeyOf(e)
  // runs 원천이 있으면 그것이 기준. 없을 때만 감사 이벤트 자체의 알람 정보를 쓴다.
  const c = (key ? runContext.get(key) : null) ?? alarmContextOf(e)
  if (!c) return { primary: e.entity_id, secondary: null, mono: true }
  return { primary: alarmDisplayLabel(c), secondary: e.entity_id, mono: false }
}

// ── 단계(phase) — 색은 예외에만. 정상 이벤트는 navy 한 계열의 명도 사다리로 생명주기를 표현하고
// 실패만 red 를 쓴다. 시작(연함) → 가설·승인(중간) → 완료·전송(진함) / 실패(red).
export const EVENT_PHASE = {
  DETECTION_COMPLETED: 'start',
  AGENT_RUN_STARTED: 'start',
  HYPOTHESIS_GENERATED: 'mid',
  APPROVAL_REQUESTED: 'mid',
  APPROVAL_DECIDED: 'mid',
  ACTION_SENT: 'done',
  AGENT_RUN_COMPLETED: 'done',
  ACTION_SEND_FAILED: 'fail',
  AGENT_RUN_FAILED: 'fail',
}
export const eventPhase = (type) => EVENT_PHASE[type] ?? 'start'
export const isFailure = (type) => eventPhase(type) === 'fail'

// 표시 — 알약 없이 점 하나 + 글자. 점만 navy 명도 사다리(L72 → L50 → L20)를 들고 실패는 fail.
// 색은 같은 hue(277°)에서 lightness 만 다르다 — 순서는 명도로, 예외는 hue 로 (Brewer sequential + accent).
export const PHASE_DOT = {
  start: 'var(--color-navy-2)',
  mid: 'var(--color-navy-3)',
  done: 'var(--color-navy)',
  fail: 'var(--color-fail)',
}
export const PHASE_TEXT = {
  start: 'text-g1',
  mid: 'text-ink',
  done: 'text-ink font-medium',
  fail: 'text-fail font-semibold',
}

// 같은 Agent 실행에 속한 이벤트끼리 타임라인 연결선을 긋기 위한 key
export function runKeyOf(e) {
  if (e.entity_type === 'AGENT_RUN') return e.entity_id
  const pairs = [...parseDetail(e.after), ...parseDetail(e.detail), ...parseDetail(e.before)]
  return pairs.find(([k]) => k === 'agent_run_id')?.[1] ?? null
}

// ── 날짜 그룹 — 행마다 날짜를 반복하지 않고 날짜가 바뀔 때만 구분 헤더를 낸다 ──
const WEEKDAY = ['일', '월', '화', '수', '목', '금', '토']
export function fmtDateHeading(ymd) {
  if (!ymd) return ''
  const [y, m, d] = ymd.split('-').map(Number)
  const dt = new Date(y, m - 1, d)
  return `${y}년 ${m}월 ${d}일 ${WEEKDAY[dt.getDay()]}요일`
}
export const dateOf = (iso) => (iso ? String(iso).slice(0, 10) : '')

// 표의 상세 열 — 유형 열이 말하지 못하는 것만 사람 말로 한 줄. 식별자 hash 는 드로어 원본에만 남긴다.
// 반환은 [text, tone] 조각 배열. tone 은 'ink' | 'muted' | 'fail'.
export function rowSummary(e, ctx) {
  const a = Object.fromEntries(parseDetail(e.after))
  const d = Object.fromEntries(parseDetail(e.detail))
  const v = (k) => a[k] ?? d[k]
  const has = (k) => v(k) != null && v(k) !== 'null' && v(k) !== ''
  switch (e.event_type) {
    case 'AGENT_RUN_STARTED': {
      const parts = []
      if (has('lot_id')) parts.push([`로트 ${v('lot_id')}`, 'ink'])
      if (has('wafer_no')) parts.push([`웨이퍼 W${Number(v('wafer_no'))}`, 'ink'])
      // 챔버는 R03 이면 대상 열에 이미 있다 — 없을 때만 보충
      if (has('chamber_id') && ctx?.source !== 'R03') parts.push([`챔버 ${v('chamber_id')}`, 'ink'])
      return parts
    }
    case 'HYPOTHESIS_GENERATED': {
      const parts = []
      if (has('predicted_fault_code')) parts.push([faultLabel(String(v('predicted_fault_code'))), 'ink'])
      const c = pct(v('confidence'))
      if (c) parts.push([`신뢰도 ${c}`, 'muted'])
      return parts
    }
    case 'APPROVAL_REQUESTED':
      return [[has('action_code') ? `${actionLabel(String(v('action_code')))} 조치` : '설비 정지 조치', 'ink'], ['담당자 승인 대기', 'muted']]
    case 'APPROVAL_DECIDED': {
      const who = has('decided_by') ? `${v('decided_by')} ` : ''
      const s = v('status')
      const parts = [[`${who}${s === 'APPROVED' ? '승인' : s === 'REJECTED' ? '반려' : s === 'EXPIRED' ? '기한 만료' : '결정'}`, s === 'REJECTED' || s === 'EXPIRED' ? 'fail' : 'ink']]
      if (has('decision_comment')) parts.push([String(v('decision_comment')), 'muted'])
      else if (has('reason')) parts.push([String(v('reason')), 'muted'])
      return parts
    }
    case 'ACTION_SENT':
      return [[`${CHANNEL_LABEL[v('channel')] ?? v('channel') ?? '지정 채널'} 전송`, 'ink']]
    case 'ACTION_SEND_FAILED': {
      const parts = [[`${CHANNEL_LABEL[v('channel')] ?? v('channel') ?? '지정 채널'} 전송 실패`, 'fail']]
      parts.push([has('error') ? String(v('error')) : '재전송 필요', 'muted'])
      return parts
    }
    case 'AGENT_RUN_COMPLETED':
      return has('action_code') ? [[`조치 ${actionLabel(String(v('action_code')))}`, 'ink']] : []
    case 'AGENT_RUN_FAILED':
      return [[has('error') ? String(v('error')) : has('reason') ? String(v('reason')) : '조치 생성 없음', 'muted']]
    default: {
      // 알 수 없는 이벤트는 키·값을 그대로 (식별자 제외)
      return Object.entries(a)
        .filter(([k]) => !ALARM_KEYS.has(k) && !/_id$/.test(k))
        .map(([k, val]) => [`${detailKeyLabel(k)} ${humanValue(k, val)}`, 'ink'])
    }
  }
}

// ── 사람이 읽는 설명 — 드로어용. 관리자는 JSON 이 아니라 "무슨 일이 있었나"를 읽는다 ──
export const FAULT_LABEL = {
  FOC: '포커스 이탈',
  RFM: 'RF 정합 불량',
  MFD: '가스 유량 이탈',
  TMD: '정전척 온도 이상',
  OTH: '기타 원인',
}
export const faultLabel = (code) => (code ? (FAULT_LABEL[code] ? `${FAULT_LABEL[code]} (${code})` : String(code)) : null)
export const ACTION_LABEL = { EQP_HOLD: '설비 정지', WARNING: '경고 알림', MONITORING: '모니터링' }
export const actionLabel = (code) => (code ? (ACTION_LABEL[code] ? `${ACTION_LABEL[code]} (${code})` : String(code)) : null)
export const CHANNEL_LABEL = { EMAIL: '이메일', MES: 'MES', MES_MOCK: 'MES' }

const pct = (v) => (v == null || Number.isNaN(Number(v)) ? null : `${Math.round(Number(v) * 100)}%`)

// 값 표기 — 키를 알므로 공장 용어로 풀어 쓴다 (fault 코드 → 이름, 신뢰도 → %)
export function humanValue(key, v) {
  if (v == null || v === 'null') return '—'
  if (key === 'predicted_fault_code' || key === 'fault_code') return faultLabel(String(v))
  if (key === 'action_code') return actionLabel(String(v))
  if (key === 'confidence') return pct(v) ?? String(v)
  if (key === 'channel') return CHANNEL_LABEL[String(v)] ?? String(v)
  return detailValueLabel(v)
}

// before/after 를 항목별 변화 목록으로. 대표 알람은 헤더에 있으니 제외.
export function changeRows(e) {
  const before = Object.fromEntries(parseDetail(e.before))
  const after = Object.fromEntries(parseDetail(e.after))
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter((k) => !ALARM_KEYS.has(k))
  return keys.map((k) => ({
    key: k,
    label: detailKeyLabel(k),
    before: k in before ? humanValue(k, before[k]) : null,
    after: k in after ? humanValue(k, after[k]) : null,
    changed: k in before && k in after && before[k] !== after[k],
  }))
}

// 이벤트 한 줄 설명
export function describeEvent(e, ctx) {
  const after = Object.fromEntries(parseDetail(e.after))
  const where = ctx?.chamberId ? `${ctx.chamberId} 챔버` : '해당 설비'
  switch (e.event_type) {
    case 'DETECTION_COMPLETED':
      return `${where}의 이상 탐지가 완료되었습니다.`
    case 'AGENT_RUN_STARTED':
      return `${where} 알람에 대해 Agent 원인 분석을 시작했습니다.`
    case 'HYPOTHESIS_GENERATED': {
      const f = faultLabel(after.predicted_fault_code)
      const c = pct(after.confidence)
      return f ? `원인을 ${f}으로 추정했습니다${c ? ` (신뢰도 ${c})` : ''}.` : '원인 가설을 생성했습니다.'
    }
    case 'APPROVAL_REQUESTED':
      return '설비 정지(EQP_HOLD) 조치에 대해 담당자 승인을 요청했습니다. 승인 전까지 설비에는 아무것도 전송되지 않습니다.'
    case 'APPROVAL_DECIDED': {
      const who = after.decided_by ? `${after.decided_by} 담당자가` : '담당자가'
      if (after.status === 'APPROVED') return `${who} 조치를 승인했습니다. 이제 설비로 전송됩니다.`
      if (after.status === 'REJECTED') return `${who} 조치를 반려했습니다. 설비는 그대로 유지됩니다.`
      if (after.status === 'EXPIRED') return '승인 기한이 지나 요청이 만료되었습니다.'
      return '승인 결정이 기록되었습니다.'
    }
    case 'ACTION_SENT':
      return `조치가 ${CHANNEL_LABEL[after.channel] ?? after.channel ?? '지정 채널'}로 전송되었습니다.`
    case 'ACTION_SEND_FAILED':
      return `조치 전송에 실패했습니다${after.channel ? ` (${CHANNEL_LABEL[after.channel] ?? after.channel})` : ''}. 재전송이 필요합니다.`
    case 'AGENT_RUN_COMPLETED':
      return 'Agent 분석이 완료되었습니다.'
    case 'AGENT_RUN_FAILED':
      return 'Agent 분석이 중단되었습니다. 이 실행에서는 조치가 생성되지 않았습니다.'
    default:
      return `${eventLabel(e.event_type)} 이벤트가 기록되었습니다.`
  }
}

// 같은 실행의 이벤트만 시간순(오래된 것부터)
export function runTimeline(items, e) {
  const key = runKeyOf(e)
  if (!key) return []
  return items
    .filter((x) => runKeyOf(x) === key)
    .slice()
    .sort((a, b) => String(a.occurred_at).localeCompare(String(b.occurred_at)))
}
export function groupByDate(items) {
  const groups = []
  for (const e of items) {
    const date = dateOf(e.occurred_at)
    const last = groups[groups.length - 1]
    if (last && last.date === date) last.items.push(e)
    else groups.push({ date, items: [e] })
  }
  return groups
}
