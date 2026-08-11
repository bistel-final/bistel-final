import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getAction, getRun } from '../../../shared/api/agent.js'
import { fmtDateTime } from '../../../shared/api/format.js'
<<<<<<< Updated upstream
=======
import EmptyState from '../../../shared/components/EmptyState.jsx'
>>>>>>> Stashed changes
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import Badge from '../../../shared/components/ui/Badge.jsx'
import { approvalClass, approvalLabel } from '../../../shared/components/ui/statusStyles.js'

const SEND_LABEL = {
  WAITING: '전송 대기',
  SENDING: '전송 중',
  SENT: '전송 완료',
  FAILED: '전송 실패',
  CANCELED: '전송 취소',
}

// 값이 없으면 창작하지 않고 "—" 로 표기한다 (규칙: 데이터 창작 금지)
const DASH = '—'

function Field({ label, children }) {
  return (
    <div>
      <div className="font-mono text-[10.5px] text-g1">{label}</div>
      <div className="mt-1 text-[12.5px] font-semibold text-ink">{children}</div>
    </div>
  )
}

// 조치 상세 — 목록 행을 펼치면 getAction(id)으로 단건을 다시 조회한다.
// 원인 분류(fault_code·fault_name·cause_summary)는 매핑 상수가 아니라 연결된 Agent 런에서 받는다.
// 패널이 열릴 때만 부르므로 목록 조회에는 부담이 없다.
// 디자인 v2 간소화: soft 박스 안에 kv 그리드 + 원인 분류 + 연관 알람만 남긴다
function ActionDetailPanel({ actionId }) {
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState(null)

  // actionId가 바뀌면 load가 새로 만들어져 useEffect가 다시 돈다.
  // setState는 전부 then/catch 안에서만 호출한다 (react-hooks/set-state-in-effect)
  const load = useCallback(() => {
    getAction(actionId)
      .then((data) =>
        // 런 조회가 실패해도 조치 상세는 그대로 보여준다 — 원인 분류만 "—"로 떨어진다
        data?.agent_run_id
          ? getRun(data.agent_run_id).then(
              (run) => ({ data, run }),
              () => ({ data, run: null }),
            )
          : { data, run: null },
      )
      .then(({ data, run }) => setDetail({ id: actionId, data, run }))
      .catch((e) => setError({ id: actionId, message: e.message }))
  }, [actionId])
  useEffect(() => {
    load()
  }, [load])

  const err = error && error.id === actionId ? error.message : null
  const current = detail && detail.id === actionId ? detail : null
  const action = current?.data ?? null
  const run = current?.run ?? null

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
  if (!action && detail?.id === actionId)
    return <EmptyState title="해당 조치를 찾을 수 없습니다" description={actionId} />
  if (!action) return <LoadingState message="조치 상세를 불러오는 중…" />

<<<<<<< Updated upstream
  // GET /agent/runs/{id} 응답 그대로 — 값이 없으면 창작하지 않고 "—" 카드로 떨어진다
  const fault = run?.fault_code ? { code: run.fault_code, name: run.fault_name, cause: run.cause_summary } : null
=======
>>>>>>> Stashed changes
  const approval = action.approval_status

  return (
    <div className="flex animate-[om-fadein_.2s] flex-col gap-3 rounded-lg border border-line bg-soft px-4 py-3.5">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-x-[18px] gap-y-2.5">
        <Field label="조치 ID">
          <span className="font-mono font-bold text-navy">{action.action_id}</span>
        </Field>
        <Field label="Agent 런">
<<<<<<< Updated upstream
          <Link to={`/agent-runs/${action.agent_run_id}`} className="font-mono font-bold">
            {action.agent_run_id}
          </Link>
=======
          {action.created_by_agent_run_id ? (
            <Link to={`/agent-runs/${action.created_by_agent_run_id}`} className="font-mono font-bold">
              {action.created_by_agent_run_id}
            </Link>
          ) : (
            <span className="font-mono text-g2">legacy · 연결 없음</span>
          )}
>>>>>>> Stashed changes
        </Field>
        <Field label="LOT · 챔버">
          <span className="font-mono font-bold">
            {action.incident.lot_id} · {action.incident.chamber_id}
          </span>
        </Field>
        <Field label="파라미터">
          <span className="font-mono font-bold">{action.sensor_id}</span>
        </Field>
        <Field label="발송 채널">
          <span className="font-mono font-bold">{action.send_channel || DASH}</span>
        </Field>
        <Field label="승인">
          <span className={`font-bold ${approvalClass(approval)}`}>{approvalLabel(approval)}</span>
          {approval === 'APPROVED' && (
            <span className="ml-1.5 font-mono text-[11.5px] font-semibold text-g1">
              {action.approved_by} · {fmtDateTime(action.approved_at)}
            </span>
          )}
        </Field>
        <Field label="전송">
          {action.send_status === 'SENT' ? (
            <Badge variant="t-green">SENT</Badge>
          ) : (
            <span className="font-semibold text-g1">{SEND_LABEL[action.send_status] ?? action.send_status}</span>
          )}
        </Field>
        <Field label="생성 시각">
          <span className="font-mono font-bold">{fmtDateTime(action.created_at)}</span>
        </Field>
      </div>

      {action.reason ? (
        <div className="rounded-lg border border-line bg-white px-3.5 py-3">
<<<<<<< Updated upstream
          <div className="flex items-center gap-2">
            <Badge variant="t-blue">{fault.code}</Badge>
            <span className="text-[13px] font-extrabold text-navy">{fault.name}</span>
          </div>
          <div className="mt-1.5 text-[12.5px] font-semibold text-g1">{fault.cause || DASH}</div>
        </div>
      ) : (
        // 연결된 런이 없거나 런에 fault_code 가 없는 경우
        <div className="text-[12.5px] font-semibold text-g2">원인 분류 {DASH}</div>
=======
          <div className="text-[11px] font-bold text-g1">조치 근거</div>
          <div className="mt-1.5 text-[12.5px] font-semibold text-ink">{action.reason}</div>
        </div>
      ) : (
        <div className="text-[12.5px] font-semibold text-g2">조치 근거 {DASH}</div>
>>>>>>> Stashed changes
      )}

      <div className="flex items-center gap-2 text-[12.5px] font-bold text-g1">
        <span>incident 알람</span>
        <span className="font-mono font-extrabold text-navy">{action.alarm_count}건</span>
        {action.created_by_agent_run_id && (
          <Link to={`/agent-runs/${action.created_by_agent_run_id}`} className="ml-1 font-sans text-xs font-bold">
            생성 실행에서 보기 →
          </Link>
        )}
      </div>
    </div>
  )
}

export default ActionDetailPanel
