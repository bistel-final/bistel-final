import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getAction, getApprovals, getRun } from '../../../shared/api/agent.js'
import { getAlarms, getTraceCatalog } from '../../../shared/api/detection.js'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import RunContextBar from '../components/RunContextBar.jsx'
import RunVerdictCard from '../components/RunVerdictCard.jsx'
import RunActionCard from '../components/RunActionCard.jsx'
import RunApprovalCard from '../components/RunApprovalCard.jsx'
import RunTransitionCard from '../components/RunTransitionCard.jsx'
import RunToolCallsCard from '../components/RunToolCallsCard.jsx'
import RunEvidencePanel from '../components/RunEvidencePanel.jsx'
import RunToast from '../components/RunToast.jsx'

const TOAST_MS = 5000
const DECIDED_LABEL = { APPROVED: '승인 완료', REJECTED: '반려' }

// Agent 분석 · 승인 (디자인 v2) — 네이비 헤더 바 · 좌 360px 판정 컬럼 · 우 근거 카드 5개
function AgentRunPage() {
  const { runId } = useParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [toast, setToast] = useState(null)
  const [decided, setDecided] = useState(null)

  const showToast = useCallback((t) => {
    const next = { ...t }
    setToast(next)
    window.setTimeout(() => setToast((cur) => (cur === next ? null : cur)), TOAST_MS)
  }, [])

  // 로더는 useCallback으로 감싸고 useEffect에서는 호출만 한다 (effect 본문 setState 금지)
  const load = useCallback(() => {
    getRun(runId)
      .then((run) => {
        if (!run) return { run: null }
        return Promise.all([getAction(run.action_id), getApprovals(), getAlarms(), getTraceCatalog()]).then(
          ([action, approvals, alarmPage, catalog]) => {
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
              catalog,
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

  useEffect(() => {
    load()
  }, [load])

  const derived = useMemo(() => {
    if (!data?.run) return null
    const { run, alarms, approvals, action } = data
    const ids = new Set(run.alarm_ids ?? [])
    const runAlarms = alarms.filter((a) => ids.has(a.alarm_id)).sort((a, b) => a.occurred_at.localeCompare(b.occurred_at))
    const ruleCnt = runAlarms.reduce((acc, a) => ({ ...acc, [a.rule_id]: (acc[a.rule_id] ?? 0) + 1 }), {})
    return {
      runAlarms,
      equipmentId: runAlarms[0]?.equipment_id ?? null,
      consec: runAlarms.find((a) => a.rule_id === 'R03_CONSEC') ?? null,
      rules: Object.entries(ruleCnt).map(([rule_id, count]) => ({ rule_id, count })),
      approval: approvals.find((p) => p.action_id === action?.action_id) ?? null,
    }
  }, [data])

  const retry = () => {
    setError(null)
    setData(null)
    load()
  }

  if (error) return <ErrorState title="Agent 실행을 불러오지 못했습니다" detail={error} onRetry={retry} />
  if (!data) return <LoadingState message="Agent 실행 결과를 불러오는 중…" />
  if (!data.run)
    return (
      <EmptyState
        title="해당 Agent 실행을 찾을 수 없습니다"
        description={`run_id ${runId} 에 대한 실행 기록이 없습니다.`}
      />
    )

  const { run, action, catalog } = data
  const { runAlarms, equipmentId, consec, rules, approval } = derived

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <RunToast toast={toast} onClose={() => setToast(null)} />

      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[22px] font-extrabold text-navy">Agent 분석 · 승인</div>
        <div className="text-xs text-g1">대시보드 「검토」 에서 바로 들어온다</div>
      </div>

      <RunContextBar run={run} equipmentId={equipmentId} />

      <div className="mt-5 flex items-start gap-5">
        {/* 좌 360px 판정 컬럼 — 흰 카드, 좌측 3px 적색 보더 */}
        <div className="flex w-[360px] flex-none flex-col gap-4 rounded-[10px] border border-line border-l-[3px] border-l-red bg-white p-5">
          <RunVerdictCard run={run} />
          <RunActionCard action={action} consec={consec} rules={rules} />
          <RunApprovalCard
            action={action}
            approval={approval}
            decided={decided}
            onDecided={setDecided}
            onToast={showToast}
          />
          <RunTransitionCard />
          <RunToolCallsCard toolCalls={run.tool_calls} nodes={run.nodes} />
        </div>

        <RunEvidencePanel
          run={run}
          runAlarms={runAlarms}
          allAlarms={data.alarms}
          catalog={catalog}
          equipmentId={equipmentId}
        />
      </div>
    </div>
  )
}

export default AgentRunPage
