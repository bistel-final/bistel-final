import { Link } from 'react-router-dom'
import Badge from '../../../shared/components/ui/Badge.jsx'
import { Card, DashedCard } from '../../../shared/components/ui/Card.jsx'
import { actionCodeVariant, approvalClass, approvalLabel } from '../../../shared/components/ui/statusStyles.js'
import AlarmMiniTrace from './AlarmMiniTrace.jsx'

// 시안 .btn.btn-primary.btn-sm 을 Link에 입힌 클래스
const BTN_PRIMARY_SM =
  'inline-flex h-7 items-center justify-center gap-1.5 rounded-lg border border-transparent bg-blue px-3.5 text-xs font-bold text-white hover:bg-navy hover:text-white'

// "06-03 06:45:45" — 시안 헤더 표기(초 포함)
const fmtShortSec = (iso) => {
  if (!iso) return ''
  const [d, t = ''] = iso.split('T')
  return `${d.slice(5)} ${t.slice(0, 8)}`
}

// alarm: AlarmItem (incident 는 (lot_id, chamber_id) 두 개뿐 — sensor_id 등은 형제 필드)
// siblings: 같은 incident 알람 목록 · wafer/limit: POST /traces/search 응답 · run: GET /agent/runs/{id}
function AlarmDetailPanel({ alarm, siblings = [], wafer, limit, run, onSelect }) {
  const pos = siblings.findIndex((a) => a.alarm_id === alarm.alarm_id)

  // 권고 조치는 Agent 실행 응답이 원본 — 없으면 알람에 붙은 조치 코드로 대체한다
  const actionCode = run?.recommended_action ?? alarm.action_code ?? null

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
          <span className="text-[10.5px] font-semibold text-g2">incident 실측</span>
        </div>
        <AlarmMiniTrace wafer={wafer} limit={limit} />
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
        {run ? (
          <>
            <div className="flex items-baseline gap-2.5">
              <span className="font-mono text-xl font-extrabold text-red">{run.fault_code || '—'}</span>
              <span className="text-sm font-bold text-ink">{run.fault_name ?? '실측 미제공'}</span>
            </div>
            <div className="mt-2 text-xs text-ink">{run.cause_summary ?? '원인 문구 실측 미제공'}</div>
          </>
        ) : (
          <div className="text-xs font-semibold text-g1">Agent 분석 없음</div>
        )}
        <div className="mt-3.5 flex flex-wrap items-center gap-2.5 border-t border-tint-red-line pt-3">
          <span className="text-[11.5px] text-g1">조치</span>
          {alarm.action_id && alarm.latest_agent_run_id ? (
            <Link to={`/agent-runs/${alarm.latest_agent_run_id}`} className="font-mono text-xs font-bold text-navy">
              {alarm.action_id}
            </Link>
          ) : (
            <span className="font-mono text-xs font-bold text-g2">—</span>
          )}
          {actionCode && (
            <Badge variant={actionCode === 'EQP_HOLD' ? 'bg-red' : actionCodeVariant(actionCode)}>{actionCode}</Badge>
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
