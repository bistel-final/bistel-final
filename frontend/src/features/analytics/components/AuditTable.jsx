import { fmtTime } from '../../../shared/api/format.js'
import { TH_CLS } from '../../../shared/components/ui/statusStyles.js'
import {
  PHASE_DOT,
  PHASE_TEXT,
  actorLabel,
  eventLabel,
  eventPhase,
  fmtDateHeading,
  groupByDate,
  isFailure,
  primaryTargetOf,
  rowSummary,
  runKeyOf,
} from './auditModel.js'

// 감사로그 테이블 — 화면 7 우측. 단순하게:
//   · 유형은 알약 없이 점 + 글자. 점만 명도 사다리를 들고 실패는 적갈 — 색은 예외에만
//   · 같은 Agent 실행의 연속 이벤트는 점 사이를 얇은 선으로 잇는다 (표가 타임라인으로 읽힘)
//   · 실패 행은 좌측 3px 레일 — 스크롤하며 스캔할 때 문제 지점만 잡힌다
//   · 날짜는 바뀔 때만 구분 헤더로, 행에는 시각만. mono 는 시각·식별자에만
// 행 클릭 → 상세 드로어.

const ROW_H = 46
const TD = 'border-b border-cell-line px-3 align-middle text-[12.5px]'

export function EventMark({ type, className = '' }) {
  const phase = eventPhase(type)
  return (
    <span className={`inline-flex items-center gap-2.5 whitespace-nowrap text-[12.5px] ${PHASE_TEXT[phase]} ${className}`}>
      <span className="h-2 w-2 flex-none rounded-full" style={{ background: PHASE_DOT[phase] }} />
      {eventLabel(type)}
    </span>
  )
}

const TONE = { ink: 'text-ink', muted: 'text-g2', fail: 'text-fail font-semibold' }

// 상세 열 — 이벤트별 사람 말 한 줄 (rowSummary). 구분 기호 없이 간격으로만 조각을 나눈다.
function Summary({ parts }) {
  if (parts.length === 0) return <span className="text-faint">—</span>
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[12.5px]">
      {parts.map(([text, tone], i) => (
        <span key={i} className={TONE[tone] ?? TONE.ink}>
          {text}
        </span>
      ))}
    </div>
  )
}

// 연결선 — 위·아래 인접 행이 같은 실행이면 점에서 셀 경계까지 선을 긋는다
function Connector({ up, down }) {
  if (!up && !down) return null
  const base = 'absolute left-[15.5px] w-px bg-navy-1'
  return (
    <>
      {up && <span className={`${base} top-0 h-1/2`} aria-hidden="true" />}
      {down && <span className={`${base} bottom-0 h-1/2`} aria-hidden="true" />}
    </>
  )
}

function AuditTable({ items, runContext = new Map(), selectedId, onSelect }) {
  const groups = groupByDate(items)
  return (
    <div className="px-2">
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className={`${TH_CLS} w-[84px] border-l-[3px] border-l-transparent`}>시각</th>
            <th className={`${TH_CLS} w-[190px]`}>유형</th>
            <th className={`${TH_CLS} w-[220px]`}>대상</th>
            <th className={TH_CLS}>상세</th>
          </tr>
        </thead>
        <tbody>
          {groups.map(({ date, items: rows }) => (
            <DateGroup key={date} date={date} rows={rows} runContext={runContext} selectedId={selectedId} onSelect={onSelect} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function DateGroup({ date, rows, runContext, selectedId, onSelect }) {
  const keys = rows.map(runKeyOf)
  return (
    <>
      <tr>
        <td colSpan={4} className="border-b border-cell-line bg-soft px-3 py-1.5 text-[11px] font-semibold text-g2">
          {fmtDateHeading(date)}
        </td>
      </tr>
      {rows.map((e, i) => {
        const fail = isFailure(e.event_type)
        const sel = selectedId === e.audit_id
        const target = primaryTargetOf(e, runContext)
        const key = keys[i]
        const parts = rowSummary(e, key ? runContext.get(key) : null)
        const up = key != null && i > 0 && keys[i - 1] === key
        const down = key != null && i < rows.length - 1 && keys[i + 1] === key
        return (
          <tr
            key={e.audit_id}
            onClick={() => onSelect?.(e)}
            className={`cursor-pointer transition-colors ${sel ? 'bg-row-sel' : 'hover:bg-soft'}`}
            style={{ height: ROW_H }}
          >
            <td className={`${TD} whitespace-nowrap border-l-[3px] font-mono text-g2 ${fail ? 'border-l-fail' : sel ? 'border-l-blue' : 'border-l-transparent'}`}>
              {fmtTime(e.occurred_at)}
            </td>
            <td className={`${TD} relative whitespace-nowrap`} style={{ height: ROW_H }}>
              <Connector up={up} down={down} />
              <EventMark type={e.event_type} className="relative" />
              {e.actor_type && e.actor_type !== 'AGENT' && (
                <span className="ml-2 text-[11.5px] text-g2">
                  {actorLabel(e.actor_type)}
                  {e.actor_id ? ` ${e.actor_id}` : ''}
                </span>
              )}
            </td>
            <td className={`${TD} whitespace-nowrap`}>
              <div className={`text-[12.5px] font-medium text-navy ${target.mono ? 'font-mono' : ''}`}>{target.primary}</div>
            </td>
            <td className={`${TD} max-w-[520px]`}>
              <Summary parts={parts} />
            </td>
          </tr>
        )
      })}
    </>
  )
}

export default AuditTable
