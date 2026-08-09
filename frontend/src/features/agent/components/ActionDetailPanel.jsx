import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getAction } from '../../../shared/api/agent.js'
import { fmtDateTime } from '../../../shared/api/format.js'
import { FAULT_BY_SENSOR } from '../mock/actions.js'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import StatusBadge from '../../../shared/components/StatusBadge.jsx'

const APPROVAL_LABEL = { AUTO: '자동', PENDING: '승인 대기', APPROVED: '승인 완료' }
const APPROVAL_TONE = { AUTO: 'info', PENDING: 'ooc', APPROVED: 'ok' }
const SEND_LABEL = { WAITING: '전송 대기', SENDING: '전송 중', SENT: '전송 완료', FAILED: '전송 실패' }
const SEND_TONE = { WAITING: 'neutral', SENDING: 'blue', SENT: 'ok', FAILED: 'oos' }

// 값이 없으면 창작하지 않고 "—" 로 표기한다 (규칙: 데이터 창작 금지)
const DASH = '—'

function Field({ label, children }) {
  return (
    <div className="flex flex-col gap-[3px]">
      <span className="text-[11.5px] font-bold text-slate-light">{label}</span>
      <span className="text-[13px] font-semibold text-ink">{children}</span>
    </div>
  )
}

// 조치 상세 — 목록 행을 펼치면 getAction(id)으로 단건을 다시 조회한다
function ActionDetailPanel({ actionId }) {
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState(null)

  // actionId가 바뀌면 load가 새로 만들어져 useEffect가 다시 돈다.
  // setState는 전부 then/catch 안에서만 호출한다 (react-hooks/set-state-in-effect)
  const load = useCallback(() => {
    getAction(actionId)
      .then((data) => setDetail({ id: actionId, data }))
      .catch((e) => setError({ id: actionId, message: e.message }))
  }, [actionId])
  useEffect(() => {
    load()
  }, [load])

  const err = error && error.id === actionId ? error.message : null
  const action = detail && detail.id === actionId ? detail.data : null

  if (err)
    return (
      <ErrorState
        title="조치 상세를 불러오지 못했습니다"
        detail={err}
        onRetry={() => {
          setError(null)
          load()
        }}
      />
    )
  if (!action) return <LoadingState message="조치 상세를 불러오는 중…" />

  const fault = FAULT_BY_SENSOR[action.incident.sensor_id]
  const approval = action.approval_status
  const send = action.send_status

  return (
    <div className="flex animate-[om-fadein_.2s] flex-col gap-3.5">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-x-5 gap-y-3">
        <Field label="조치 ID">
          <span className="font-mono font-extrabold text-navy">{action.action_id}</span>
        </Field>
        <Field label="Agent 런">
          <Link to={`/agent-runs/${action.run_id}`} className="font-mono font-bold">
            {action.run_id}
          </Link>
        </Field>
        <Field label="LOT · 챔버">
          <span className="font-mono font-bold">
            {action.incident.lot_id} · {action.incident.chamber_id}
          </span>
        </Field>
        <Field label="파라미터">
          <span className="font-mono font-bold">{action.incident.sensor_id}</span>
        </Field>
        <Field label="발송 채널">
          <span className="font-mono font-bold">{action.channel || DASH}</span>
        </Field>
        <Field label="승인">
          <span className="flex flex-wrap items-center gap-1.5">
            <StatusBadge tone={APPROVAL_TONE[approval] ?? 'neutral'}>
              {APPROVAL_LABEL[approval] ?? approval}
            </StatusBadge>
            {approval === 'APPROVED' && (
              <span className="font-mono text-[12px] font-semibold text-slate">
                {action.approved_by || DASH} · {fmtDateTime(action.approved_at) || DASH}
              </span>
            )}
          </span>
        </Field>
        <Field label="전송">
          <StatusBadge tone={SEND_TONE[send] ?? 'neutral'}>{SEND_LABEL[send] ?? send}</StatusBadge>
        </Field>
        <Field label="생성 시각">
          {/* TODO(data): action_history.csv 미확보로 created_at이 null인 건이 있다 — 추정 생성하지 않고 "—" */}
          <span className="font-mono font-bold">{fmtDateTime(action.created_at) || DASH}</span>
        </Field>
      </div>

      {fault ? (
        <div className="rounded-lg border border-line bg-white px-3.5 py-3">
          <div className="flex items-center gap-2">
            <StatusBadge tone="info" mono>
              {fault.code}
            </StatusBadge>
            <span className="text-[13px] font-extrabold text-navy">{fault.name}</span>
          </div>
          <div className="mt-1.5 text-[12.5px] font-semibold text-slate">{fault.cause}</div>
        </div>
      ) : (
        // TODO(data): 센서별 Fault 분류가 없는 파라미터
        <div className="text-[12.5px] font-semibold text-slate-light">원인 분류 {DASH}</div>
      )}

      <div>
        <div className="mb-1.5 flex items-center gap-2 text-[12.5px] font-bold text-slate">
          <span>연관 알람</span>
          <span className="font-mono font-extrabold text-navy">{action.alarm_count}건</span>
          <Link
            to={`/alarms?alarms=${action.alarm_ids.join(',')}`}
            className="ml-1 font-sans text-[12px] font-bold"
          >
            알람 목록에서 보기 →
          </Link>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {action.alarm_ids.map((id) => (
            <Link
              key={id}
              to={`/alarms/${id}`}
              className="rounded-md border border-line-input bg-white px-2 py-[3px] font-mono text-[11.5px] font-extrabold text-slate no-underline hover:border-brand hover:text-brand"
            >
              {id}
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}

export default ActionDetailPanel
