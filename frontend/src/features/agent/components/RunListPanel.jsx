import { fmtShort } from '../../../shared/api/format.js'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import Badge from '../../../shared/components/ui/Badge.jsx'
import { actionCodeVariant, runStatusVariant } from '../../../shared/components/ui/statusStyles.js'
import { FaultBadge } from './faultStyles.jsx'

// 자동 분석 실행 리스트 — 라이트 시안 3번 좌측 286px
// 카드: run id 모노 700 + 챔버 + fault 뱃지 + 권고 조치 뱃지 · 선택 시 tint-blue 하이라이트
function RunListPanel({ runs, selectedId, onSelect }) {
  return (
    <Card className="w-[286px] flex-none">
      <CardHeader title="자동 분석 실행" note={`${runs.length}건`} />
      <div className="flex max-h-[calc(100vh-220px)] flex-col gap-2 overflow-y-auto px-3 pb-3.5">
        {runs.map((r) => {
          const on = r.agent_run_id === selectedId
          return (
            <button
              key={r.agent_run_id}
              type="button"
              onClick={() => onSelect(r.agent_run_id)}
              className={`cursor-pointer rounded-[10px] border p-3 text-left ${
                on ? 'border-tint-blue-line bg-tint-blue' : 'border-line bg-white hover:border-tint-blue-line'
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-mono text-[11.5px] font-bold text-ink">{r.agent_run_id}</span>
                <span className="flex-none font-mono text-[10px] text-g2">{fmtShort(r.started_at)}</span>
              </div>
              <div className="mt-1 font-mono text-[11px] text-g1">{r.incident?.chamber_id}</div>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <Badge variant={runStatusVariant(r.status)}>{r.status ?? '상태 미제공'}</Badge>
                <FaultBadge code={r.fault_code} />
                <Badge variant={actionCodeVariant(r.recommended_action)}>{r.recommended_action ?? '조치 미정'}</Badge>
              </div>
            </button>
          )
        })}
      </div>
    </Card>
  )
}

export default RunListPanel
