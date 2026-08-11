import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
<<<<<<< Updated upstream
import { getAction, getApprovals, getRun } from '../../../shared/api/agent.js'
import { getAlarms, searchTraces } from '../../../shared/api/detection.js'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
=======
import { getRun } from '../../../shared/api/agent.js'
>>>>>>> Stashed changes
import EmptyState from '../../../shared/components/EmptyState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import RunActionCard from '../components/RunActionCard.jsx'
import RunApprovalCard from '../components/RunApprovalCard.jsx'
import RunContextBar from '../components/RunContextBar.jsx'
import RunEvidencePanel from '../components/RunEvidencePanel.jsx'
import RunToast from '../components/RunToast.jsx'
import RunToolCallsCard from '../components/RunToolCallsCard.jsx'
import RunTransitionCard from '../components/RunTransitionCard.jsx'
import RunVerdictCard from '../components/RunVerdictCard.jsx'

const POLL_MS = 2000
const TOAST_MS = 5000
<<<<<<< Updated upstream
const DECIDED_LABEL = { APPROVED: '승인 완료', REJECTED: '반려' }
// 근거 카드 ②④ 는 incident 밖 알람까지 훑는다 — GET /alarms 에 alarm_ids 파라미터가 없어 넓게 받는다
// TODO(api): alarm_ids 필터 파라미터 미정의
const ALARM_SCAN_SIZE = 200
=======
const POLL_STATUSES = new Set(['RUNNING'])
>>>>>>> Stashed changes

function AgentRunPage() {
  const { runId } = useParams()
  const [run, setRun] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [toast, setToast] = useState(null)
  const [decided, setDecided] = useState(null)
  const timerRef = useRef(null)

  const showToast = useCallback((value) => {
    const next = { ...value }
    setToast(next)
    window.setTimeout(() => setToast((current) => (current === next ? null : current)), TOAST_MS)
  }, [])

<<<<<<< Updated upstream
  // 로더는 useCallback으로 감싸고 useEffect에서는 호출만 한다 (effect 본문 setState 금지)
  const load = useCallback(() => {
    getRun(runId)
      .then((run) => {
        if (!run) return { run: null }
        return Promise.all([
          getAction(run.action_id),
          getApprovals(),
          getAlarms({ page: 1, size: ALARM_SCAN_SIZE }),
          // 문제 파라미터 차트 — 한계선은 응답 limits[sensor_id] 에서 읽는다 (전역 상수 없음)
          searchTraces({
            chamber_id: run.incident?.chamber_id,
            sensor_ids: [run.sensor_id],
            lot_id: run.incident?.lot_id,
          }),
        ]).then(
          ([action, approvals, alarmPage, trace]) => {
            // 이미 처리된 승인 건은 진입 시점에 사유를 알린다 (서버 재결정은 409)
            if (action && action.approval_status !== 'PENDING' && action.approval_status !== 'AUTO') {
              showToast({
                tone: 'oos',
                title: '409 CONFLICT',
                message: `이미 ${DECIDED_LABEL[action.approval_status] ?? action.approval_status} 처리된 승인 건입니다. 재결정할 수 없습니다.`,
              })
            }
            return {
              run,
              action,
              approvals: approvals.items ?? [],
              alarms: alarmPage.items ?? [],
              trace,
            }
          },
        )
      })
      .then((d) => {
        setDecided(null)
        setData(d)
      })
      .catch((e) => setError(e.message))
  }, [runId, showToast])
=======
  const load = useCallback(
    ({ quiet = false } = {}) =>
      getRun(runId)
        .then((next) => {
          setRun(next)
          setError(null)
        })
        .catch((requestError) => setError(requestError.message))
        .finally(() => {
          if (!quiet) setLoading(false)
        }),
    [runId],
  )
>>>>>>> Stashed changes

  useEffect(() => {
    load()
  }, [load])

<<<<<<< Updated upstream
  const derived = useMemo(() => {
    if (!data?.run) return null
    const { run, alarms, approvals } = data
    const ids = new Set(run.alarm_ids ?? [])
    const runAlarms = alarms.filter((a) => ids.has(a.alarm_id)).sort((a, b) => a.occurred_at.localeCompare(b.occurred_at))
    const ruleCnt = runAlarms.reduce((acc, a) => ({ ...acc, [a.rule_id]: (acc[a.rule_id] ?? 0) + 1 }), {})
    return {
      runAlarms,
      equipmentId: run.equipment_id ?? runAlarms[0]?.equipment_id ?? null,
      consec: runAlarms.find((a) => a.rule_id === 'R03_CONSEC') ?? null,
      rules: Object.entries(ruleCnt).map(([rule_id, count]) => ({ rule_id, count })),
      // 승인 요청은 run 과 agent_run_id 로 이어진다 (run_id 아님)
      approval: approvals.find((p) => p.agent_run_id === run.agent_run_id) ?? null,
    }
  }, [data])
=======
  useEffect(() => {
    window.clearInterval(timerRef.current)
    if (!POLL_STATUSES.has(run?.status)) return undefined
    timerRef.current = window.setInterval(() => load({ quiet: true }), POLL_MS)
    return () => window.clearInterval(timerRef.current)
  }, [load, run?.status])
>>>>>>> Stashed changes

  const onDecided = (result) => {
    setDecided(result)
    setRun((current) =>
      current
        ? {
            ...current,
            status: result.agent_run_status ?? current.status,
            action: current.action
              ? {
                  ...current.action,
                  approval_status: result.status,
                  send_status: result.send_status ?? current.action.send_status,
                }
              : null,
            approval: current.approval
              ? {
                  ...current.approval,
                  status: result.status,
                  decided_by: result.decided_by,
                  decision_comment: result.comment,
                }
              : null,
          }
        : current,
    )
    if (result.agent_run_status === 'RUNNING') load({ quiet: true })
  }

  if (error && !run)
    return (
      <ErrorState
        title="Agent 실행을 불러오지 못했습니다"
        detail={error}
        onRetry={() => {
          setLoading(true)
          load()
        }}
      />
    )
  if (loading) return <LoadingState message="Agent 실행 결과를 불러오는 중…" />
  if (!run)
    return (
      <EmptyState
        title="해당 Agent 실행을 찾을 수 없습니다"
        description={`agent_run_id ${runId} 에 대한 실행 기록이 없습니다.`}
      />
    )

<<<<<<< Updated upstream
  const { run, action, trace } = data
  const { runAlarms, equipmentId, consec, rules, approval } = derived

=======
>>>>>>> Stashed changes
  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <RunToast toast={toast} onClose={() => setToast(null)} />

      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[22px] font-extrabold text-navy">Agent 분석 · 승인</div>
        <div className="text-xs text-g1">
          {POLL_STATUSES.has(run.status) ? '2초 간격으로 실행 상태를 갱신합니다.' : '저장된 실행 결과'}
        </div>
      </div>

      <RunContextBar run={run} equipmentId={run.equipment_id} />

      <div className="mt-5 flex items-start gap-5">
        <div className="flex w-[360px] flex-none flex-col gap-4 rounded-[10px] border border-line border-l-[3px] border-l-red bg-white p-5">
          <RunVerdictCard run={run} />
<<<<<<< Updated upstream
          <RunActionCard run={run} consec={consec} rules={rules} />
=======
          <RunActionCard action={run.action} reason={run.action_reason} />
>>>>>>> Stashed changes
          <RunApprovalCard
            action={run.action}
            approval={run.approval}
            decided={decided}
            onDecided={onDecided}
            onToast={showToast}
          />
          {run.status === 'WAITING_APPROVAL' && <RunTransitionCard />}
          <RunToolCallsCard toolCalls={run.tool_calls ?? []} />
        </div>

<<<<<<< Updated upstream
        <RunEvidencePanel
          run={run}
          runAlarms={runAlarms}
          allAlarms={data.alarms}
          trace={trace}
          equipmentId={equipmentId}
        />
=======
        <RunEvidencePanel run={run} />
>>>>>>> Stashed changes
      </div>
    </div>
  )
}

export default AgentRunPage
