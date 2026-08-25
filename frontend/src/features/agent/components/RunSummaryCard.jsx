import { fmtDateTime } from '../../../shared/api/format.js'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import { detailNumbers, limitLines } from '../../detection/components/TraceModel.jsx'

// 알람 요약 카드 — 라이트 시안 3번 우측 스택 3번
// 상단 요약 문장(블루 soft 박스) + 4열 KV 그리드 12항목
// 값은 전부 응답 실측에서만 만든다 — 없는 값은 "실측 미제공"
const AREA_BY_PREFIX = { PHO: 'PHOTO', ETC: 'ETCH' }
const areaOf = (id) => AREA_BY_PREFIX[String(id ?? '').slice(0, 3)] ?? '—'

const measuredOf = (alarm) => {
  if (!alarm) return null
  const { mean, min, max } = detailNumbers(alarm.detail)
  return max ?? min ?? mean
}

function RunSummaryCard({ run, repAlarm, lim, action }) {
  const judgement = repAlarm?.judgement ?? (run.recommended_action === 'MONITOR' ? 'OOC' : 'OOS')
  const bound = judgement === 'OOS' ? 'USL' : 'UCL'
  const measured = measuredOf(repAlarm)
  const limitText = limitLines(lim)
    .map((l) => `${l.label === 'TARGET' ? 'TGT' : l.label} ${l.value}`)
    .join(' · ')

  const summary = `${run.incident?.chamber_id}에서 ${run.sensor_id} 값이 ${bound}을 벗어나는 ${judgement} 알람 발생. Agent는 ${run.fault_name}(${run.fault_code})로 분류하고 ${run.recommended_action} 조치를 권고했습니다.`

  const items = [
    ['발생 시각', fmtDateTime(run.incident_first_at)],
    ['AREA', areaOf(run.equipment_id)],
    ['설비 · 챔버', `${run.equipment_id} · ${run.incident?.chamber_id}`],
    ['RECIPE STEP', run.recipe_step_name],
    ['LOT · WAFER', repAlarm ? `${repAlarm.lot_id} · W${repAlarm.wafer_no}` : run.incident?.lot_id],
    ['PARAMETER', run.sensor_id],
    ['측정값', measured != null ? `${measured}${lim?.unit ? ` ${lim.unit}` : ''}` : '실측 미제공', judgement === 'OOS' ? 'text-red' : 'text-tint-amber-text'],
    ['한계선', limitText || '한계선 미제공'],
    ['STEP · SEQ', repAlarm ? `${repAlarm.recipe_step_name} · ${repAlarm.recipe_step_no}` : run.recipe_step_name],
    ['알람 유형', `${judgement} · ${run.alarm_count}건`],
    ['알림 발송', action ? `${action.send_channel} · ${action.send_status}` : '—'],
    ['FAULT 분류', `${run.fault_code} · ${run.fault_name}`],
  ]

  return (
    <Card>
      <CardHeader title="알람 요약" note={`알람 ${run.alarm_count}건 incident`} />
      <div className="px-5 pb-4">
        <div className="rounded-[10px] border border-[#dbeafe] bg-tint-blue px-4 py-3 text-[12.5px] leading-[1.65] text-ink">
          {summary}
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
