import { fmtDateTime } from '../../../shared/api/format.js'
import Badge from '../../../shared/components/ui/Badge.jsx'
import { CELL_DIM, TD_CLS, TH_CLS, rowClass } from '../../../shared/components/ui/statusStyles.js'
import {
  TONE_BADGE,
  actorLabel,
  detailKeyLabel,
  detailValueLabel,
  eventLabel,
  eventTone,
  parseDetail,
  primaryTargetOf,
} from './auditModel.js'

// 감사로그 테이블 — 라이트 시안 7번 우측 (한글화·4톤 틴트, 흰 사이드바 기준)
// 시간(mono) / 행위자(틴트 배지, 한글) / 유형(4톤 틴트 배지, 한글) / 대상(알람 ID 우선, 실행·조치 ID 보조)
// / 상세(키·값 한글 칩 + before→after) / 결과
const ACTOR_BADGE = { AGENT: 't-navy', HUMAN: 't-blue', USER: 't-blue', SYSTEM: 't-gray' }

const stateText = (v) => {
  if (v == null) return null
  if (typeof v !== 'object' || Array.isArray(v)) return detailValueLabel(v)
  return Object.entries(v)
    .map(([k, val]) => `${detailKeyLabel(k)} ${detailValueLabel(val)}`)
    .join(' · ')
}

// 결과 — 실패·반려 red / 승인·성공 green (틴트 텍스트)
function resultOf(e) {
  if (String(e.event_type).endsWith('_FAILED')) return { label: '실패', cls: 'font-bold text-red' }
  if (e.event_type === 'APPROVAL_DECIDED') {
    if (e.after?.status === 'APPROVED') return { label: '승인', cls: 'font-bold text-green-dark' }
    if (e.after?.status === 'REJECTED') return { label: '반려', cls: 'font-bold text-red' }
  }
  return { label: '성공', cls: 'text-green-dark' }
}

function DetailChips({ detail }) {
  const pairs = parseDetail(detail)
  if (pairs.length === 0) return null
  // 대표 알람은 대상 열로 올라갔으니 상세에서는 중복 표시하지 않는다
  const shown = pairs.filter(([k]) => k !== 'representative_alarm_id' && k !== 'alarm_id')
  if (shown.length === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px]" title={typeof detail === 'string' ? detail : ''}>
      {shown.map(([k, v], i) =>
        k ? (
          <span key={i} className="whitespace-nowrap">
            <span className="text-g2">{detailKeyLabel(k)}</span>{' '}
            <span className="font-mono font-semibold text-ink">{detailValueLabel(v)}</span>
          </span>
        ) : (
          <span key={i} className="text-g1">
            {v}
          </span>
        ),
      )}
    </div>
  )
}

function AuditTable({ items }) {
  return (
    <div className="overflow-x-auto px-2">
      <table className="w-full border-collapse">
        <thead>
          <tr>
            {['시간', '행위자', '유형', '대상', '상세', '결과'].map((h) => (
              <th key={h} className={TH_CLS}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((e, i) => {
            const res = resultOf(e)
            const before = stateText(e.before)
            const after = stateText(e.after)
            const target = primaryTargetOf(e)
            return (
              <tr key={e.audit_id} className={rowClass(i)}>
                <td className={`${TD_CLS} ${CELL_DIM}`}>{fmtDateTime(e.occurred_at)}</td>
                <td className={TD_CLS}>
                  <span className="flex items-center gap-1.5">
                    <Badge variant={ACTOR_BADGE[e.actor_type] ?? 't-gray'}>{actorLabel(e.actor_type)}</Badge>
                    {e.actor_id && e.actor_id !== e.actor_type && (
                      <span className="font-mono text-[10.5px] text-g2">{e.actor_id}</span>
                    )}
                  </span>
                </td>
                <td className={TD_CLS}>
                  <Badge variant={TONE_BADGE[eventTone(e.event_type)]}>{eventLabel(e.event_type)}</Badge>
                </td>
                <td className={TD_CLS}>
                  <div className="font-mono text-[12.5px] font-bold text-navy">{target.primary}</div>
                  {target.secondary && <div className="font-mono text-[10.5px] text-g2">{target.secondary}</div>}
                </td>
                <td className={`${TD_CLS} max-w-[420px]`}>
                  <DetailChips detail={e.detail} />
                  {(before || after) && (
                    <div
                      className={`truncate font-mono text-[10.5px] text-g2 ${e.detail ? 'mt-0.5' : ''}`}
                      title={`${before ?? '—'} → ${after ?? '—'}`}
                    >
                      {before ? (
                        <>
                          {before} <span className="text-faint">→</span>{' '}
                        </>
                      ) : null}
                      {after ?? '—'}
                    </div>
                  )}
                  {!e.detail && !before && !after && <span className="text-[12px] text-faint">—</span>}
                </td>
                <td className={`${TD_CLS} text-[12px] ${res.cls}`}>{res.label}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export default AuditTable
