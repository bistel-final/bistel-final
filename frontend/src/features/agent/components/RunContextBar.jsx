import Badge from '../../../shared/components/ui/Badge.jsx'
import { fmtShort, fmtTime } from '../../../shared/api/format.js'

// 값이 없으면 창작하지 않고 "—" 로 표기한다
const DASH = '—'

// run 상태별 solid 배지 — WAITING_APPROVAL 황(시안), RUNNING 청, COMPLETED 녹, FAILED 적
const STATUS_VARIANT = {
  WAITING_APPROVAL: 'bg-amber',
  RUNNING: 'bg-blue',
  COMPLETED: 'bg-green',
  FAILED: 'bg-red',
}

function Kv({ k, v }) {
  return (
    <span className="flex flex-col gap-1">
      <span className="text-[10.5px] text-side-dim">{k}</span>
      <span className="font-mono text-[13px] font-bold">{v || DASH}</span>
    </span>
  )
}

// 네이비 헤더 바 — RUN ID · 상태 배지 · 설비/챔버/파라미터/LOT/기간 5개 kv
function RunContextBar({ run, equipmentId }) {
  const inc = run.incident
  const { sensor_id, incident_first_at, incident_last_at } = run
  // 설비는 알람 실측에서, 없으면 챔버 ID 규칙(PHO-01-C1 → PHO-01)에서 유도
  const equipment = equipmentId ?? (inc.chamber_id ? inc.chamber_id.replace(/-C\d+$/, '') : null)

  // 기간은 incident 실측 first_at~last_at — 같은 날이면 뒤쪽은 시각만 남긴다
  const first = fmtShort(incident_first_at)
  const last = fmtShort(incident_last_at)
  const sameDay = first.slice(0, 5) === last.slice(0, 5)
  const period = first && last ? `${first} ~ ${sameDay ? fmtTime(incident_last_at) : last}` : DASH

  return (
    <div className="flex items-center gap-9 rounded-[10px] bg-navy px-6 py-4 text-white">
      <span className="font-mono text-[15px] font-extrabold">{run.run_id}</span>
      <Badge variant={STATUS_VARIANT[run.status] ?? 'bg-gray'}>{run.status}</Badge>
      <Kv k="설비" v={equipment} />
      <Kv k="챔버" v={inc.chamber_id} />
      <Kv k="파라미터" v={sensor_id} />
      <Kv k="LOT" v={inc.lot_id} />
      <Kv k="기간" v={period} />
    </div>
  )
}

export default RunContextBar
