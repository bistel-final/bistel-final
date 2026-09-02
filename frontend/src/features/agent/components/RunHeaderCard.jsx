import { fmtDateTime } from '../../../shared/api/format.js'
import { Card } from '../../../shared/components/ui/Card.jsx'
import Badge from '../../../shared/components/ui/Badge.jsx'
import { actionCodeVariant, runStatusVariant } from '../../../shared/components/ui/statusStyles.js'
import { FaultBadge } from './faultStyles.jsx'
import { alarmDisplayLabel, approvalStatusSummary, runStatusText } from './agentModel.js'

// 헤더 카드 — 라이트 시안 3번 우측 스택 1번
// 상단: run id·챔버·발생시각 / 모델명·지연시간(우측)
// 하단 행: Fault 뱃지 · 신뢰도(120px 바) · 권고 조치 뱃지 · 승인 상태 텍스트
function RunHeaderCard({ run, action, approval }) {
  const approvalSummary = approvalStatusSummary(action, approval)
  const hasConfidence = run.confidence != null
  const conf = hasConfidence ? Math.round(run.confidence * 100) : null
  const alarmSource = run.representative_alarm_source ?? run.alarm_source
  const alarmLabel = alarmDisplayLabel({
    source: alarmSource,
    alarmId: run.representative_alarm_id ?? run.alarm_id,
    chamberId: run.incident?.chamber_id,
    lotId: run.incident?.lot_id,
  })
  return (
    <Card className="agent-main-readable px-5 py-4" data-agent-run-id={run.agent_run_id}>
      <div className="flex items-start justify-between">
        <div>
          <div className="text-[15px] font-extrabold text-ink" title={`실행 ID: ${run.agent_run_id}`}>
            {alarmLabel} 분석
          </div>
          <div className="mt-1 font-mono text-[11.5px] text-g1">
            {run.incident?.chamber_id} · 발생 {fmtDateTime(run.incident_first_at)}
          </div>
        </div>
        <div className="text-right font-mono text-[11px] text-g2">
          <div>{run.llm_model}</div>
          <div className="mt-0.5">{run.latency_ms?.toLocaleString()}ms</div>
        </div>
      </div>
      <div className="mt-3.5 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-cell-line pt-3.5">
        <Badge variant={runStatusVariant(run.status)}>{runStatusText(run.status)}</Badge>
        <FaultBadge code={run.fault_code} name={run.fault_name} />
        <span className="flex items-center gap-2">
          <span className="text-[11px] font-bold text-g2">신뢰도</span>
          {hasConfidence ? (
            <>
              <span className="h-[7px] w-[120px] overflow-hidden rounded-full bg-cell-line">
                <span className="block h-full rounded-full bg-blue" style={{ width: `${conf}%` }} />
              </span>
              <span className="font-mono text-[11.5px] font-bold text-ink">{conf}%</span>
            </>
          ) : (
            <span className="font-mono text-[11.5px] font-bold text-g2">판단 미완료</span>
          )}
        </span>
        <Badge variant={actionCodeVariant(run.recommended_action)}>{run.recommended_action ?? '조치 미정'}</Badge>
        <span className="text-[12.5px] font-bold text-g1">{approvalSummary}</span>
      </div>
    </Card>
  )
}

export default RunHeaderCard
