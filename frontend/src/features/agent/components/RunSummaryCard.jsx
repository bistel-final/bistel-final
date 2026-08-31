import { Link } from 'react-router-dom'
import { fmtDateTime } from '../../../shared/api/format.js'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import { detailNumbers, limitLines } from '../../../shared/trace/traceModel.js'
import { alarmJudgement, measuredText } from '../agent-run-view-state.js'

// 알람 요약 카드 — 라이트 시안 3번 우측 스택 3번
// 상단 요약 문장(블루 soft 박스) + 4열 KV 그리드 12항목
// 값은 전부 응답 실측에서만 만든다 — 없는 값은 "실측 미제공"
const measuredOf = (alarm) => {
  if (!alarm) return null
  if (alarm.value != null) return Number(alarm.value)
  const { mean, min, max } = detailNumbers(alarm.detail)
  return max ?? min ?? mean
}

function RunSummaryCard({ run, repAlarm, lim, action, alarmHref = null }) {
  const judgement = alarmJudgement(run, repAlarm)
  const bound = judgement === 'OOS' ? 'USL' : judgement === 'OOC' ? 'UCL' : null
  const measured = measuredOf(repAlarm)
  const limitText = limitLines(lim)
    .map((l) => `${l.label === 'TARGET' ? 'TGT' : l.label} ${l.value}`)
    .join(' · ')

  const faultLabel = run.fault_name ? `${run.fault_name}(${run.fault_code})` : (run.fault_code ?? '미분류 Fault')
  const alarmSource = run.representative_alarm_source ?? run.alarm_source
  const summary = repAlarm && bound
    ? `${measuredText(run.incident?.chamber_id)}에서 ${measuredText(run.sensor_id, '대표 파라미터')} 값이 ${bound}을 벗어나는 ${judgement} 알람 발생. Agent는 ${faultLabel}로 분류하고 ${run.recommended_action ?? '추가 확인'} 조치를 권고했습니다.`
    : judgement
      ? `${measuredText(run.incident?.chamber_id)}에서 ${alarmSource} ${judgement} 알람 발생. Agent는 ${faultLabel}로 분류하고 ${run.recommended_action ?? '추가 확인'} 조치를 권고했습니다.`
      : `${measuredText(run.incident?.chamber_id)}의 알람 판정 실측이 제공되지 않았습니다. Agent 분류와 조치 상세에서 실행 근거를 확인해 주세요.`

  const lotWafer = repAlarm
    ? [repAlarm.lot_id, repAlarm.wafer_id ?? (repAlarm.wafer_no != null ? `W${repAlarm.wafer_no}` : null)].filter(Boolean).join(' · ')
    : run.incident?.lot_id
  const stepSeq = repAlarm
    ? [repAlarm.recipe_step_name, repAlarm.recipe_step_no].filter((value) => value != null && value !== '').join(' · ')
    : run.recipe_step_name

  const items = [
    ['발생 시각', measuredText(fmtDateTime(run.incident_first_at))],
    ['AREA', measuredText(repAlarm?.area)],
    ['설비 · 챔버', [run.equipment_id, run.incident?.chamber_id].filter(Boolean).join(' · ') || '실측 미제공'],
    ['RECIPE STEP', measuredText(run.recipe_step_name)],
    ['LOT · WAFER', measuredText(lotWafer)],
    ['PARAMETER', measuredText(run.sensor_id)],
    ['측정값', measured != null ? `${measured}${lim?.unit ? ` ${lim.unit}` : ''}` : '실측 미제공', judgement === 'OOS' ? 'text-red' : 'text-tint-amber-text'],
    ['한계선', limitText || '한계선 미제공'],
    ['STEP · SEQ', measuredText(stepSeq)],
    ['알람 유형', `${judgement ?? '판정 미제공'} · ${run.alarm_count}건`],
    [
      '알림 발송',
      action?.deliveries?.length
        ? action.deliveries.map((delivery) => `${delivery.channel} · ${delivery.status}`).join(' / ')
        : '발송 내역 없음',
    ],
    ['FAULT 분류', run.fault_name ? `${run.fault_code} · ${run.fault_name}` : measuredText(run.fault_code, '미분류')],
  ]

  return (
    <Card>
      <CardHeader title="알람 요약" note={`알람 ${run.alarm_count}건 incident`} />
      <div className="px-5 pb-4">
        <div className="rounded-[10px] border border-[#dbeafe] bg-tint-blue px-4 py-3 text-[12.5px] leading-[1.65] text-ink">
          {summary}
          {alarmHref && (
            <Link to={alarmHref} className="ml-2 whitespace-nowrap font-bold text-blue">
              대표 알람 보기 →
            </Link>
          )}
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
    </Card>
  )
}

export default RunSummaryCard
