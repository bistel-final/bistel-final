import { Link } from 'react-router-dom'
import Badge from '../../../shared/components/ui/Badge.jsx'
import { Card, DashedCard } from '../../../shared/components/ui/Card.jsx'
import { actionCodeVariant, approvalClass, approvalLabel } from '../../../shared/components/ui/statusStyles.js'
import AlarmMiniTrace from './AlarmMiniTrace.jsx'

// 파라미터 → fault code 매핑. agent feature의 FAULT_BY_SENSOR와 값을 동일하게 맞춘 로컬 상수
// (feature 간 import 금지 규칙 때문에 복제한다 — 원본이 바뀌면 함께 갱신할 것)
const FAULT_BY_PARAM = {
  PH_FOCUS: { code: 'FOC', name: '포커스 이탈', cause: 'PH_FOCUS 연속 OOS — 포커스 이탈로 CD_ADI 불량 유발' },
  ET_REFL: { code: 'RFM', name: 'RF 정합 이상', cause: '반사파 상승 = RF 정합 불량, 실효 전력 저하' },
  ET_CF4: { code: 'MFD', name: 'CF4 유량 저하', cause: 'CF4 유량 저하로 식각 부족' },
}

// 시안 .btn.btn-primary.btn-sm 을 Link에 입힌 클래스
const BTN_PRIMARY_SM =
  'inline-flex h-7 items-center justify-center gap-1.5 rounded-lg border border-transparent bg-blue px-3.5 text-xs font-bold text-white hover:bg-navy hover:text-white'

// 같은 incident = lot_id + chamber_id + sensor_id
const incidentKey = (a) =>
  `${a.incident?.lot_id ?? a.lot_id}|${a.incident?.chamber_id ?? a.chamber_id}|${a.incident?.sensor_id ?? a.sensor_id}`

// "06-03 06:45:45" — 시안 헤더 표기(초 포함)
const fmtShortSec = (iso) => {
  if (!iso) return ''
  const [d, t = ''] = iso.split('T')
  return `${d.slice(5)} ${t.slice(0, 8)}`
}

function AlarmDetailPanel({ alarm, alarms, catalog, area, onSelect }) {
  const siblings = alarms
    .filter((a) => incidentKey(a) === incidentKey(alarm))
    .sort((a, b) => a.occurred_at.localeCompare(b.occurred_at) || a.alarm_id.localeCompare(b.alarm_id))
  const pos = siblings.findIndex((a) => a.alarm_id === alarm.alarm_id)

  const waferTrace = (catalog?.wafers ?? []).find((w) => w.lot_hist_id === alarm.lot_hist_id) ?? null

  const traceQuery = new URLSearchParams({
    area,
    equipment: alarm.equipment_id ?? '',
    chamber: alarm.chamber_id ?? '',
    sensor: alarm.sensor_id ?? '',
    lot: alarm.lot_id ?? '',
    wafer: alarm.wafer_no ?? '',
  }).toString()

  const fault = FAULT_BY_PARAM[alarm.sensor_id] ?? null

  return (
    <aside className="flex animate-[om-fadein_.2s_ease-out] flex-col gap-3.5 rounded-[10px] border border-line border-l-[3px] border-l-blue bg-white p-5">
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-lg font-extrabold text-navy">{alarm.alarm_id}</span>
        <span className="text-[11.5px] text-g1">선택한 알람</span>
      </div>

      <div>
        <div className="font-mono text-[12.5px] font-bold text-ink">
          {alarm.lot_id} · W{alarm.wafer_no} · {alarm.chamber_id}
        </div>
        <div className="mt-1 font-mono text-[11px] text-g1">
          {alarm.recipe_step_name} · {alarm.rule_id} · {alarm.judgement} · {fmtShortSec(alarm.occurred_at)}
        </div>
      </div>

      <Card className="rounded-lg px-3.5 pb-2 pt-3.5">
        <div className="mb-1.5 flex items-center justify-between">
          <span className="font-mono text-[12.5px] font-bold text-navy">{alarm.sensor_id}</span>
          <Link to={`/traces?${traceQuery}`} className={BTN_PRIMARY_SM}>
            크게 보기 →
          </Link>
        </div>
        <AlarmMiniTrace wafer={waferTrace} limits={catalog?.limits} />
      </Card>

      <div className="rounded-lg border border-line bg-soft p-3.5">
        <div className="mb-2.5 text-[12.5px] font-bold text-navy">
          같은 incident 알람 {siblings.length}건 중 {pos + 1}번째
        </div>
        <div className="flex flex-wrap gap-1.5">
          {siblings.map((s) => {
            const on = s.alarm_id === alarm.alarm_id
            return (
              <button
                key={s.alarm_id}
                type="button"
                onClick={() => onSelect(s.alarm_id)}
                className={`inline-flex h-[26px] cursor-pointer items-center rounded-md border px-2.5 font-mono text-[10.5px] font-semibold ${
                  on ? 'border-blue bg-blue text-white' : 'border-line bg-white text-g1'
                }`}
              >
                {s.alarm_id}
              </button>
            )
          })}
        </div>
        <div className="mt-2.5 text-[11px] text-g1">좌우 알람으로 바로 이동</div>
      </div>

      <div className="rounded-lg border border-tint-red-line bg-row-red p-4">
        <div className="mb-3 text-[13px] font-extrabold text-red">Agent 판단</div>
        {fault ? (
          <>
            <div className="flex items-baseline gap-2.5">
              <span className="font-mono text-xl font-extrabold text-red">{fault.code}</span>
              <span className="text-sm font-bold text-ink">{fault.name}</span>
            </div>
            <div className="mt-2 text-xs text-ink">{fault.cause}</div>
          </>
        ) : (
          // TODO(data): 이 파라미터의 fault code·원인 실측 미제공
          <div className="text-xs font-semibold text-g1">fault code 실측 미제공</div>
        )}
        <div className="mt-3.5 flex flex-wrap items-center gap-2.5 border-t border-tint-red-line pt-3">
          <span className="text-[11.5px] text-g1">조치</span>
          {alarm.action_id ? (
            <Link to={`/actions?action=${alarm.action_id}`} className="font-mono text-xs font-bold text-navy">
              {alarm.action_id}
            </Link>
          ) : (
            <span className="font-mono text-xs font-bold text-g2">—</span>
          )}
          {alarm.action_code && (
            <Badge variant={alarm.action_code === 'EQP_HOLD' ? 'bg-red' : actionCodeVariant(alarm.action_code)}>
              {alarm.action_code}
            </Badge>
          )}
          {alarm.approval_status && (
            <span className={`text-xs font-bold ${approvalClass(alarm.approval_status)}`}>
              {approvalLabel(alarm.approval_status)}
            </span>
          )}
        </div>
        {alarm.latest_agent_run_id && (
          <div className="mt-3 flex justify-end">
            <Link to={`/agent-runs/${alarm.latest_agent_run_id}`} className={BTN_PRIMARY_SM}>
              분석 보기 →
            </Link>
          </div>
        )}
      </div>

      <DashedCard className="px-[18px] py-4">
        <div className="mb-2 text-[13px] font-extrabold text-navy">URL 이 함께 바뀐다</div>
        <div className="font-mono text-xs font-semibold text-blue">/alarms/{alarm.alarm_id}</div>
        <div className="mt-2 text-[11.5px] leading-[1.6] text-g1">
          선택 상태가 주소에 남아야 공유되고
          <br />
          뒤로가기가 목록으로 돌아온다.
        </div>
        <div className="mt-2 text-[11.5px] font-bold text-green">이 화면은 실제로 URL 이 바뀐다.</div>
      </DashedCard>
    </aside>
  )
}

export default AlarmDetailPanel
