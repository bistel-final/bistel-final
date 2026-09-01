import { useState } from 'react'
import { fmtDateTime } from '../../../shared/api/format.js'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import { HistoryTrendChart } from '../../../shared/components/trace/HistoryTrendChart.jsx'
import { detailNumbers, limitLines } from '../../../shared/trace/traceModel.js'
import { alarmJudgement, impactOntologySelection, measuredText } from '../agent-run-view-state.js'
import AgentImpactGraphModal from './AgentImpactGraphModal.jsx'
import {
  approvalStatusSummary,
  deliveryStatusSummary,
  impactLabelOf,
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

function ImpactItems({ title, items, emptyText, checkRequired = false }) {
  return (
    <div className="mt-2.5">
      <div className="text-[10.5px] font-bold text-navy">{title}</div>
      {items?.length ? (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {items.map((item) => (
            <span
              key={`${item.kind}:${item.source_id}:${item.relation ?? ''}`}
              className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[10.5px] ${
                checkRequired
                  ? 'border-[#ead9b3] bg-[#fffaf0] text-[#6f5422]'
                  : 'border-tint-blue-line bg-white/75 text-g1'
              }`}
            >
              <span className="font-semibold">{impactLabelOf(item)}</span>
              <strong className="font-mono text-navy">{impactSourceOf(item)}</strong>
            </span>
          ))}
        </div>
      ) : (
        <div className="mt-1 text-[10.5px] text-g2">{emptyText}</div>
      )}
    </div>
  )
}

const SummaryFact = ({ label, value }) => (
  <span className="inline-flex items-center gap-1.5 rounded-md border border-tint-blue-line bg-white/75 px-2 py-1 text-[10.5px] text-g1">
    <span className="font-semibold">{label}</span>
    <strong className="font-mono text-navy">{value}</strong>
  </span>
)

function RepresentativeAlarmModal({ alarm, run, measured, wafer, lim, judgement, onClose }) {
  const waferLabel = alarm?.wafer_no != null ? `W${Number(alarm.wafer_no)}` : measuredText(alarm?.wafer_id)
  const rows = [
    ['알람 ID', measuredText(alarm?.alarm_id ?? run.representative_alarm_id)],
    ['판정', judgement ?? '판정 미제공'],
    ['발생 시각', measuredText(fmtDateTime(alarm?.occurred_at ?? run.incident_first_at))],
    ['LOT · WAFER', `${measuredText(alarm?.lot_id ?? run.incident?.lot_id)} · ${waferLabel}`],
    ['설비 · 챔버', `${measuredText(alarm?.equipment_id ?? run.equipment_id)} · ${measuredText(alarm?.chamber_id ?? run.incident?.chamber_id)}`],
    ['파라미터', measuredText(alarm?.parameter_id ?? alarm?.sensor_id ?? run.sensor_id)],
    ['공정 단계', measuredText(alarm?.recipe_step_name ?? run.recipe_step_name)],
    ['측정값', measured != null ? `${measured}${lim?.unit ? ` ${lim.unit}` : ''}` : '실측 미제공'],
    ['적용 규칙', measuredText(alarm?.rule_id)],
    ['반복 건수', alarm?.hit_cnt != null ? `${alarm.hit_cnt}건` : `${run.alarm_count}건`],
  ]
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-6" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div className="flex max-h-[calc(100vh-48px)] w-[min(1080px,calc(100vw-48px))] flex-col overflow-hidden rounded-2xl border border-line bg-white shadow-2xl" role="dialog" aria-modal="true" aria-label="대표 알람 상세">
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <div>
            <div className="text-[16px] font-extrabold text-navy">대표 알람 상세</div>
            <div className="mt-1 font-mono text-[11px] text-g2">{measuredText(alarm?.source ?? run.representative_alarm_source)} · {measuredText(alarm?.alarm_id ?? run.representative_alarm_id)}</div>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg border border-line px-3 py-2 text-[12px] font-bold text-g1 hover:bg-soft">닫기 ✕</button>
        </div>
        <div className="overflow-y-auto p-6">
          <div className="h-[360px] rounded-xl border border-cell-line bg-white p-2">
            <HistoryTrendChart wafer={wafer} lim={lim} highlightWaferNo={alarm?.wafer_no} viewMode="selected" />
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
            {rows.map(([label, value]) => (
              <div key={label} className="rounded-lg border border-cell-line bg-soft px-3.5 py-3">
                <div className="text-[10px] font-bold text-faint">{label}</div>
                <div className="mt-1 break-all font-mono text-[12.5px] font-semibold text-ink">{value}</div>
              </div>
            ))}
          </div>
          {!wafer && (
            <div className="mt-4 rounded-lg border border-dashed border-dash-line px-4 py-5 text-center text-[12px] text-g2">
              대표 알람의 trace 실측 데이터가 없어 차트를 표시할 수 없습니다.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function RunSummaryCard({ run, detail, repAlarm, wafer = null, lim, action }) {
  const [alarmOpen, setAlarmOpen] = useState(false)
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
  const approvalStatus = approvalStatusSummary(action, detail?.approval)
  const deliveryStatus = deliveryStatusSummary(action)
  const observation = detail?.post_action_observation?.message ?? '조치 후 관찰 정보 없음'
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
              {repAlarm && (
                <button type="button" onClick={() => setAlarmOpen(true)} className="shrink-0 text-[11.5px] font-bold text-blue hover:text-blue-hover">
                  대표 알람 보기 →
                </button>
              )}
            </div>
            <div className="mt-2 text-[13.5px] font-semibold leading-[1.7]">
              {llmSummary ?? '이 실행에는 저장된 LLM 원인 분석 결과가 없습니다.'}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              <SummaryFact label="대상 LOT" value={measuredText(incidentLot)} />
              <SummaryFact label="발생 WAFER" value={incidentWaferText} />
              <SummaryFact label="발생 챔버" value={measuredText(run.incident?.chamber_id)} />
              <SummaryFact label="이상 파라미터" value={measuredText(run.sensor_id, '미제공')} />
              <SummaryFact label="알람 판정" value={`${judgement ?? '미제공'} · ${run.alarm_count}건`} />
            </div>
            {detail?.diagnosis?.evidence_synthesis && (
              <div className="mt-3 border-t border-tint-blue-line pt-2.5 text-[11.5px] leading-6 text-g1">
                <strong className="text-navy">근거 종합:</strong> {detail.diagnosis.evidence_synthesis}
              </div>
            )}
          </section>
          <section className="flex h-full flex-col rounded-[10px] border border-[#dbeafe] bg-tint-blue px-3.5 py-3">
            <div className="flex items-start justify-between gap-3">
              <div className="text-[10.5px] font-extrabold text-blue-hover">영향 범위</div>
              {impactSelection && (
                <button type="button" onClick={() => setImpactOpen(true)} className="shrink-0 text-[11.5px] font-bold text-blue hover:text-blue-hover">
                  영향 범위 보기 →
                </button>
              )}
            </div>
            <div className="mt-2 text-[12px] font-semibold leading-5 text-ink">
              {impact?.summary ?? '저장된 영향 범위 요약이 없습니다.'}
            </div>
            <ImpactItems
              title="직접 영향 대상"
              items={impact?.direct}
              emptyText="확정된 직접 영향 대상이 없습니다."
            />
            <ImpactItems
              title="추가 확인 대상"
              items={impact?.check_required}
              emptyText="추가로 연쇄 영향을 확인할 대상이 없습니다."
              checkRequired
            />
          </section>
          <section className="rounded-[10px] border border-[#dbeafe] bg-tint-blue px-3.5 py-3">
            <div className="flex items-center justify-between gap-2">
              <div className="text-[10.5px] font-extrabold text-blue-hover">권고 조치</div>
              <span className="rounded-md border border-tint-blue-line bg-white px-2 py-0.5 font-mono text-[10.5px] font-bold text-blue">{actionCode}</span>
            </div>
            <div className="mt-2 text-[12px] font-semibold leading-5 text-ink">{actionReason}</div>
            <div className="mt-2 text-[11px] leading-5 text-g1"><strong className="text-navy">다음 확인:</strong> {verificationSteps}</div>
          </section>
          <section className="rounded-[10px] border border-[#dbeafe] bg-tint-blue px-3.5 py-3">
            <div className="text-[10.5px] font-extrabold text-blue-hover">승인 · 전달 · 관찰</div>
            <div className="mt-2 text-[11.5px] leading-5 text-g1"><strong className="text-navy">승인:</strong> {approvalStatus}</div>
            <div className="mt-1 text-[11.5px] leading-5 text-g1"><strong className="text-navy">전달:</strong> {deliveryStatus}</div>
            <div className="mt-1 text-[11px] leading-5 text-g2"><strong className="text-navy">조치 후:</strong> {observation}</div>
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
      {alarmOpen && repAlarm && (
        <RepresentativeAlarmModal
          alarm={repAlarm}
          run={run}
          measured={measured}
          wafer={wafer}
          lim={lim}
          judgement={judgement}
          onClose={() => setAlarmOpen(false)}
        />
      )}
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
