import { fmtDateTime } from '../../../shared/api/format.js'
import Badge from '../../../shared/components/ui/Badge.jsx'
import { CELL_DIM, TD_CLS, TH_CLS, actorVariant, rowClass } from '../../../shared/components/ui/statusStyles.js'
import { eventHex } from './auditModel.js'

// 감사로그 테이블 — 라이트 시안 7번 우측
// 시간(mono) / 행위자(solid 뱃지) / 유형(유형색 틴트 뱃지) / 대상(mono blue) / 상세(detail + before→after) / 결과
const stateText = (v) => {
  if (v == null) return null
  if (typeof v !== 'object' || Array.isArray(v)) return String(v)
  return Object.entries(v)
    .map(([k, val]) => `${k} ${val}`)
    .join(', ')
}

// 결과 — 실패·반려 red bold / 승인·성공 green (시안 고정)
function resultOf(e) {
  if (String(e.event_type).endsWith('_FAILED')) return { label: '실패', cls: 'font-bold text-red' }
  if (e.event_type === 'APPROVAL_DECIDED') {
    if (e.after?.status === 'APPROVED') return { label: '승인', cls: 'font-bold text-green-dark' }
    if (e.after?.status === 'REJECTED') return { label: '반려', cls: 'font-bold text-red' }
  }
  return { label: '성공', cls: 'text-green-dark' }
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
            const hex = eventHex(e.event_type)
            const res = resultOf(e)
            const before = stateText(e.before)
            const after = stateText(e.after)
            return (
              <tr key={e.audit_id} className={rowClass(i)}>
                <td className={`${TD_CLS} ${CELL_DIM}`}>{fmtDateTime(e.occurred_at)}</td>
                <td className={TD_CLS}>
                  <span className="flex items-center gap-1.5">
                    <Badge variant={actorVariant(e.actor_type)}>{e.actor_type}</Badge>
                    <span className="font-mono text-[10.5px] text-g2">{e.actor_id}</span>
                  </span>
                </td>
                <td className={TD_CLS}>
                  <span
                    className="inline-flex h-5 items-center whitespace-nowrap rounded-full border px-2.5 font-mono text-[10px] font-bold"
                    style={{ color: hex, background: `${hex}14`, borderColor: `${hex}40` }}
                  >
                    {e.event_type}
                  </span>
                </td>
                <td className={`${TD_CLS} font-mono font-bold text-blue`}>{e.entity_id}</td>
                <td className={`${TD_CLS} max-w-[320px]`}>
                  {/* detail 이 없으면 빈 줄을 그리지 않는다 — before→after 만 남을 때 본문 줄로 쓴다 */}
                  {e.detail && (
                    <div className="truncate text-[12px] text-g1" title={e.detail}>
                      {e.detail}
                    </div>
                  )}
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
