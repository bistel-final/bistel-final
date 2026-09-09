import { useState } from 'react'
import { fmtDateTime } from '../../../shared/api/format.js'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import { detailNumbers, limitLines } from '../../../shared/trace/traceModel.js'
import { alarmJudgement, impactOntologySelection, measuredText } from '../agent-run-view-state.js'
import AgentImpactGraphModal from './AgentImpactGraphModal.jsx'
import {
  deliveryStatusSummary,
  impactSourceOf,
} from './agentModel.js'

// 알람 요약 카드 — 라이트 시안 3번 우측 스택 3번
// 상단 요약 문장(블루 soft 박스) + 4열 KV 그리드 12항목
// 값은 전부 응답 실측에서만 만든다 — 없는 값은 "실측 미제공"
const measuredOf = (alarm, wafer) => {
  if (!alarm) return null
  if (alarm.value != null) return Number(alarm.value)
  const { mean, min, max } = detailNumbers(alarm.detail)
  if (max != null || min != null || mean != null) return max ?? min ?? mean
  const stepPoints = (wafer?.points ?? []).filter(
    (point) => alarm.recipe_step_no == null || Number(point.recipe_step_no) === Number(alarm.recipe_step_no),
  )
  return stepPoints[0]?.value ?? wafer?.points?.[0]?.value ?? null
}

const incidentWafersOf = (detail, repAlarm) => {
  const directWafers = (detail?.impact_scope?.direct ?? [])
    .filter((item) => item.kind === 'WAFER')
    .map((item) => item.source_id.split(':').at(-1))
    .filter(Boolean)
  if (directWafers.length > 0) return [...new Set(directWafers)]
  const representative = repAlarm?.wafer_id ?? (repAlarm?.wafer_no != null ? `W${repAlarm.wafer_no}` : null)
  return representative ? [representative] : []
}

const SummaryFact = ({ label, value }) => (
  <span className="inline-flex items-center gap-1.5 rounded-md border border-tint-blue-line bg-white/75 px-2 py-1 text-[10.5px] text-g1">
    <span className="font-semibold">{label}</span>
    <strong className="font-mono text-navy">{value}</strong>
  </span>
)

function RunSummaryCard({ run, detail, repAlarm, wafer = null, lim, action }) {
  const [impactOpen, setImpactOpen] = useState(false)
  const judgement = alarmJudgement(run, repAlarm)
  const measured = measuredOf(repAlarm, wafer)
  const limitText = limitLines(lim)
    .map((l) => `${l.label === 'TARGET' ? 'TGT' : l.label} ${l.value}`)
    .join(' · ')

  const llmSummary = detail?.prediction?.cause_summary?.trim()
    || detail?.diagnosis?.cause_summary?.trim()
    || run.cause_summary?.trim()
    || null
  const incidentWafers = incidentWafersOf(detail, repAlarm)
  const incidentWaferText = incidentWafers.length > 0
    ? incidentWafers.map((wafer) => impactSourceOf({ kind: 'WAFER', source_id: wafer })).join(' · ')
    : '실측 미제공'
  const incidentLot = repAlarm?.lot_id ?? run.incident?.lot_id
  const impact = detail?.impact_scope
  const verificationSteps = detail?.diagnosis?.verification_steps?.join(' → ') || '추가 확인 절차 미제공'
  const actionCode = action?.action_code ?? run.recommended_action ?? '조치 미결정'
  const actionReason = action?.reason ?? '규칙 기반 조치 사유 미제공'
  const deliveryStatus = deliveryStatusSummary(action)
  const impactSelection = impactOntologySelection(detail, run.incident?.chamber_id ?? repAlarm?.chamber_id)

  const stepSeq = repAlarm
    ? [repAlarm.recipe_step_name, repAlarm.recipe_step_no].filter((value) => value != null && value !== '').join(' · ')
    : run.recipe_step_name

  const items = [
    ['발생 시각', measuredText(fmtDateTime(run.incident_first_at))],
    ['AREA', measuredText(repAlarm?.area)],
    ['설비 · 챔버', [run.equipment_id, run.incident?.chamber_id].filter(Boolean).join(' · ') || '실측 미제공'],
    ['RECIPE STEP', measuredText(run.recipe_step_name)],
    ['LOT · 발생 WAFER', `${measuredText(incidentLot)} · ${incidentWaferText}`],
    ['PARAMETER', measuredText(run.sensor_id)],
    ['측정값', measured != null ? `${measured}${lim?.unit ? ` ${lim.unit}` : ''}` : '실측 미제공', judgement === 'OOS' ? 'text-red' : 'text-tint-amber-text'],
    ['한계선', limitText || '한계선 미제공'],
    ['STEP · SEQ', measuredText(stepSeq)],
    ['알람 유형', `${judgement ?? '판정 미제공'} · ${run.alarm_count}건`],
    [
      '알림 발송',
      deliveryStatus,
    ],
    ['FAULT 분류', run.fault_name ? `${run.fault_code} · ${run.fault_name}` : measuredText(run.fault_code, '미분류')],
  ]

  return (
    <Card className="agent-main-readable">
      <CardHeader title="Agent 분석 요약" note={`알람 ${run.alarm_count}건 incident`} />
      <div className="px-5 pb-4">
        <div className="grid grid-cols-2 gap-3" data-testid="agent-analysis-decision-summary">
          <section className="rounded-[10px] border border-[#dbeafe] bg-tint-blue px-4 py-3.5 text-ink">
            <div className="flex items-start justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2 text-[10.5px] font-extrabold text-blue-hover">
                <span>LLM 원인 분석</span>
                <span className="font-mono font-semibold text-g2">{detail?.prediction?.llm_model ?? run.llm_model ?? 'model 미제공'}</span>
              </div>
            </div>
            <div className="mt-2 text-[13.5px] font-semibold leading-[1.7]">
              {llmSummary ?? '이 실행에는 저장된 LLM 원인 분석 결과가 없습니다.'}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              <SummaryFact label="대상 LOT" value={measuredText(incidentLot)} />
              <SummaryFact label="발생 WAFER" value={incidentWaferText} />
              <SummaryFact label="발생 챔버" value={measuredText(run.incident?.chamber_id)} />
              <SummaryFact label="이상 파라미터" value={measuredText(run.sensor_id, '미제공')} />
            </div>
            {detail?.diagnosis?.evidence_synthesis && (
              <div className="mt-3 border-t border-tint-blue-line pt-2.5 text-[11.5px] leading-6 text-g1">
                <strong className="text-navy">근거 종합:</strong> {detail.diagnosis.evidence_synthesis}
              </div>
            )}
          </section>
          <section className="rounded-[10px] border border-[#dbeafe] bg-tint-blue px-3.5 py-3">
            <div className="text-[10.5px] font-extrabold text-blue-hover">권고 조치</div>
            <div className="mt-2 text-[12px] font-semibold leading-5 text-ink">{actionReason}</div>
            <div className="mt-2 text-[11px] leading-5 text-g1"><strong className="text-navy">다음 확인:</strong> {verificationSteps}</div>
            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              <SummaryFact label="조치 코드" value={actionCode} />
              <SummaryFact label="알람 판정" value={`${judgement ?? '미제공'} · ${run.alarm_count}건`} />
            </div>
            <div className="mt-2 flex items-start justify-between gap-3 border-t border-tint-blue-line pt-2.5">
              <div className="min-w-0 text-[11px] leading-5 text-g1">
                <strong className="text-navy">영향 범위:</strong> {impact?.summary ?? '저장된 영향 범위 요약이 없습니다.'}
              </div>
              {impactSelection && (
                <button type="button" onClick={() => setImpactOpen(true)} className="shrink-0 text-[11px] font-bold text-blue hover:text-blue-hover">
                  자세히 →
                </button>
              )}
            </div>
          </section>
        </div>
        <div className="mt-4 grid grid-cols-4 gap-x-5 gap-y-3.5">
          {items.map(([k, v, cls]) => (
            <div key={k} className="min-w-0">
              <div className="text-[10px] font-bold tracking-[.03em] text-faint">{k}</div>
              <div className={`mt-0.5 truncate font-mono text-[12.5px] font-semibold text-ink ${cls ?? ''}`} title={String(v)}>
                {v}
              </div>
            </div>
          ))}
        </div>
      </div>
      {impactOpen && impactSelection && (
        <AgentImpactGraphModal
          onClose={() => setImpactOpen(false)}
          selection={impactSelection}
          impactScope={impact}
        />
      )}
    </Card>
  )
}

export default RunSummaryCard
