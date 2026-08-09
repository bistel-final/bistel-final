import StatusBadge from '../../../shared/components/StatusBadge.jsx'
import { fmtDateTime, fmtTime } from '../../../shared/api/format.js'

// 값이 없으면 창작하지 않고 "—" 로 표기한다
const DASH = '—'

const STATUS_TONE = {
  WAITING_APPROVAL: 'ooc',
  RUNNING: 'blue',
  COMPLETED: 'ok',
  FAILED: 'oos',
}

const STATUS_LABEL = {
  WAITING_APPROVAL: '승인 대기',
  RUNNING: '실행 중',
  COMPLETED: '완료',
  FAILED: '실패',
}

function Field({ label, value }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-[11.5px] font-bold text-slate-light">{label}</span>
      <span className="font-mono text-[13px] font-extrabold text-ink">{value || DASH}</span>
    </span>
  )
}

// 상단 고정 컨텍스트 바 — 스크롤해도 어떤 incident를 보고 있는지 잃지 않게 한다
function RunContextBar({ run, equipmentId }) {
  const inc = run.incident
  const first = fmtDateTime(inc.first_at)
  const last = fmtDateTime(inc.last_at)
  // 같은 날이면 뒤쪽은 시각만 남겨 가독성을 높인다
  const sameDay = first.slice(0, 10) === last.slice(0, 10)
  const period = first && last ? `${first} ~ ${sameDay ? fmtTime(inc.last_at) : last}` : DASH

  return (
    <div className="sticky top-0 z-30 -mx-7 -mt-6 border-b border-line bg-white/95 px-7 py-3 backdrop-blur-sm">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <span className="font-mono text-[15px] font-extrabold text-navy">{run.run_id}</span>
        <StatusBadge tone={STATUS_TONE[run.status] ?? 'neutral'} mono>
          {STATUS_LABEL[run.status] ?? run.status}
        </StatusBadge>
        <span className="h-[18px] w-px bg-line" />
        <Field label="설비" value={equipmentId} />
        <Field label="챔버" value={inc.chamber_id} />
        <Field label="파라미터" value={inc.sensor_id} />
        <Field label="LOT" value={inc.lot_id} />
        <Field label="기간" value={period} />
        <span className="ml-auto text-[12px] font-bold text-slate">
          RECIPE STEP <span className="font-mono text-navy">{inc.recipe_step_name || DASH}</span> · 알람{' '}
          <span className="font-mono text-navy">{inc.alarm_count}</span>건
        </span>
      </div>
    </div>
  )
}

export default RunContextBar
