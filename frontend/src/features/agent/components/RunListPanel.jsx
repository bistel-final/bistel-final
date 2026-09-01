import { fmtShort } from '../../../shared/api/format.js'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import Badge from '../../../shared/components/ui/Badge.jsx'
import { actionCodeVariant, runStatusVariant } from '../../../shared/components/ui/statusStyles.js'
import { FaultBadge } from './faultStyles.jsx'
import { runStatusText } from './agentModel.js'

const ALARM_SOURCE_LABEL = Object.freeze({
  TRACE: 'TRACE 알람',
  SUMMARY: 'SUMMARY 알람',
  R03: 'R03 연속 알람',
})

// Agent 분석 실행 리스트 — 라이트 시안 3번 좌측 286px
// 카드: 분석 대상 알람 + 챔버 + fault 뱃지 + 권고 조치 뱃지 · 선택 시 tint-blue 하이라이트
function RunListPanel({ runs, selectedId, onSelect }) {
  return (
    <Card className="agent-main-readable w-[286px] flex-none">
      <CardHeader title="Agent 분석 실행" note={`${runs.length}건`} />
      <div className="flex max-h-[calc(100vh-220px)] flex-col gap-2 overflow-y-auto px-3 pb-3.5">
        {runs.map((r) => {
          const on = r.agent_run_id === selectedId
          return (
            <button
              key={r.agent_run_id}
              type="button"
              onClick={() => onSelect(r.agent_run_id)}
              aria-label={`${ALARM_SOURCE_LABEL[r.alarm_source] ?? '알람'} ${r.alarm_id ?? 'ID 미제공'} 분석 실행 보기`}
              title={`실행 ID: ${r.agent_run_id}`}
              className={`cursor-pointer rounded-[10px] border p-3 text-left ${
                on ? 'border-tint-blue-line bg-tint-blue' : 'border-line bg-white hover:border-tint-blue-line'
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-[10px] font-extrabold text-blue">
                  {ALARM_SOURCE_LABEL[r.alarm_source] ?? '알람'}
                </span>
                <span className="flex-none font-mono text-[10px] text-g2">{fmtShort(r.started_at)}</span>
              </div>
              <div className="mt-1 truncate font-mono text-[12px] font-extrabold text-ink" title={r.alarm_id ?? undefined}>
                {r.alarm_id ?? '알람 ID 미제공'}
              </div>
              <div className="mt-1 font-mono text-[11px] text-g1">{r.incident?.chamber_id ?? '챔버 미제공'}</div>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <Badge variant={runStatusVariant(r.status)}>{runStatusText(r.status)}</Badge>
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
