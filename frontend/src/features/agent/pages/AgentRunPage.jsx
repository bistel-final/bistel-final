import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { decideApprovalCanonical, getRun, getRunsCore } from '../../../shared/api/agent.js'
import { getAlarm, getParameters, getTrace } from '../../../shared/api/detection.js'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { HistoryTrendCard } from '../../detection/components/HistoryTrendChart.jsx'
import {
  adaptRunForLegacyPage,
  approvalViewState,
  evidenceHref,
  selectInitialRun,
  trendUnavailableMessage,
} from '../agent-run-view-state.js'
import RunDetailModal from '../components/RunDetailModal.jsx'
import RunHeaderCard from '../components/RunHeaderCard.jsx'
import RunListPanel from '../components/RunListPanel.jsx'
import RunSummaryCard from '../components/RunSummaryCard.jsx'

const publicErrorMessage = (error, fallback) => {
  const status = error?.response?.status
  if (status === 404) return '요청한 실행을 찾을 수 없습니다.'
  if (status === 409) return '이미 처리된 요청입니다. 최신 상태를 다시 불러왔습니다.'
  if (status === 422) return '입력값을 확인해 주세요.'
  if (status === 503) return 'Agent 조회 서비스가 잠시 준비되지 않았습니다.'
  return fallback
}

function AgentLanding() {
  const navigate = useNavigate()
  const requestRef = useRef(null)
  const [state, setState] = useState({ phase: 'loading', error: null })

  const load = useCallback(() => {
    requestRef.current?.abort()
    const controller = new AbortController()
    requestRef.current = controller
    getRunsCore({}, { signal: controller.signal }).then(
      (runs) => {
        if (controller.signal.aborted) return
        const selected = selectInitialRun(runs)
        if (selected) navigate(`/agent-runs/${encodeURIComponent(selected.agent_run_id)}`, { replace: true })
        else setState({ phase: 'empty', error: null })
      },
      (error) => {
        if (!controller.signal.aborted) {
          setState({ phase: 'error', error: publicErrorMessage(error, 'Agent 실행 목록을 불러오지 못했습니다.') })
        }
      },
    )
  }, [navigate])

  useEffect(() => {
    load()
    return () => requestRef.current?.abort()
  }, [load])

  if (state.phase === 'error') return <ErrorState title="Agent 실행 목록 오류" detail={state.error} onRetry={load} />
  if (state.phase === 'empty') {
    return <EmptyState title="아직 Agent 실행이 없습니다" description="알람에서 분석을 실행하면 이 화면에 표시됩니다." />
  }
  return <LoadingState message="Agent 실행을 선택하는 중…" />
}

const waferIdOf = (alarm) =>
  alarm?.wafer_id ??
  (alarm?.lot_id && alarm?.wafer_no != null
    ? `${alarm.lot_id}W${String(alarm.wafer_no).padStart(3, '0')}`
    : null)

const legacyRunOf = (detail, alarm) => {
  const base = adaptRunForLegacyPage(detail)
  const representativeAlarm = detail.evidence_items.find((item) => item.type === 'ALARM')
  const alarmCount = detail.evidence_items.filter((item) => item.type === 'ALARM').length
  const cause = detail.evidence_items.find((item) => item.type === 'DOCUMENT')?.excerpt ?? null
  return {
    ...base,
    fault_code: detail.predicted_fault_code ?? null,
    fault_name: detail.fault_name,
    representative_alarm_source: alarm?.source ?? representativeAlarm?.alarm.source ?? detail.alarm_source,
    sensor_id: alarm?.parameter_id ?? alarm?.sensor_id ?? null,
    recipe_step_name: alarm?.recipe_step_name ?? '실측 미제공',
    incident: { lot_id: alarm?.lot_id ?? null, chamber_id: detail.chamber_id },
    equipment_id: alarm?.equipment_id ?? base.equipment_id,
    representative_alarm_id: alarm?.alarm_id ?? detail.alarm_id,
    incident_first_at: alarm?.occurred_at ?? detail.created_at,
    incident_last_at: alarm?.occurred_at ?? detail.created_at,
    alarm_count: Math.max(alarmCount, Number(alarm?.hit_cnt ?? 0), 1),
    cause_summary: cause,
    action_reason: detail.action?.reason ?? null,
  }
}

const documentHitsOf = (detail) => ({
  hits: detail.evidence_items
    .filter((item) => item.type === 'DOCUMENT')
    .map((item) => ({
      document_id: item.document_id,
      chunk_id: item.chunk_id,
      section: item.section,
      content: item.excerpt,
      score: null,
      href: evidenceHref(item),
    })),
})

function AgentRunDetailPage({ runId }) {
  const navigate = useNavigate()
  const requestRef = useRef(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [state, setState] = useState({
    phase: 'loading',
    runs: [],
    detail: null,
    alarm: null,
    trace: [],
    parameters: [],
    trendMessage: null,
    error: null,
  })
  const [approvalState, dispatch] = useReducer(approvalViewState, { phase: 'idle', status: null, error: null })

  const load = useCallback(() => {
    requestRef.current?.abort()
    const controller = new AbortController()
    requestRef.current = controller
    Promise.all([
      getRunsCore({}, { signal: controller.signal }),
      getRun(runId, { signal: controller.signal }),
    ]).then(
      async ([runs, detail]) => {
        if (controller.signal.aborted) return
        if (detail == null) {
          setState({
            phase: 'missing',
            runs,
            detail: null,
            alarm: null,
            trace: [],
            parameters: [],
            trendMessage: null,
            error: null,
          })
          return
        }
        const alarmEvidence = detail.evidence_items.find((item) => item.type === 'ALARM')
        let alarmLookupFailed = false
        const alarm = alarmEvidence
          ? await getAlarm(alarmEvidence.alarm.alarm_id, alarmEvidence.alarm.source).catch(() => {
              alarmLookupFailed = true
              return null
            })
          : null
        const wafer = waferIdOf(alarm)
        const parameter = alarm?.parameter_id ?? alarm?.sensor_id
        const queryReady = Boolean(alarm?.lot_id && wafer && parameter)
        let traceFailed = false
        const tracePromise = queryReady
          ? getTrace({ lot: alarm.lot_id, wafer, chamber: detail.chamber_id, parameter }).catch(() => {
              traceFailed = true
              return []
            })
          : Promise.resolve([])
        const [trace, parameters] = await Promise.all([
          tracePromise,
          getParameters().catch(() => []),
        ])
        if (controller.signal.aborted) return
        const trendMessage = trendUnavailableMessage({
          hasAlarmEvidence: Boolean(alarmEvidence),
          alarmFound: Boolean(alarm),
          alarmLookupFailed,
          queryReady,
          traceFailed,
          traceCount: trace.length,
        })
        dispatch({ type: 'RESET', status: detail.approval?.status ?? detail.action?.approval_status ?? null })
        setState({
          phase: 'success',
          runs,
          detail,
          alarm,
          trace,
          parameters,
          trendMessage,
          error: null,
        })
      },
      (error) => {
        if (controller.signal.aborted) return
        setState({
          phase: error?.response?.status === 404 ? 'missing' : 'error',
          runs: [],
          detail: null,
          alarm: null,
          trace: [],
          parameters: [],
          trendMessage: null,
          error: publicErrorMessage(error, 'Agent 실행 상세를 불러오지 못했습니다.'),
        })
      },
    )
  }, [runId])

  useEffect(() => {
    load()
    return () => requestRef.current?.abort()
  }, [load])

  const view = useMemo(() => {
    if (state.phase !== 'success') return null
    const run = legacyRunOf(state.detail, state.alarm)
    const parameterId = state.alarm?.parameter_id ?? state.alarm?.sensor_id
    const parameter = state.parameters.find((item) => item.parameter_id === parameterId) ?? null
    const lim = parameter
      ? { ...parameter, target: parameter.target_value ?? parameter.TARGET }
      : null
    const wafer = state.trace.length
      ? {
          sensor_id: parameterId,
          wafer_no: state.alarm?.wafer_no,
          chamber_id: state.detail.chamber_id,
          points: state.trace,
        }
      : null
    return { run, wafer, lim, docs: documentHitsOf(state.detail) }
  }, [state])

  const decide = (decision, decidedBy, comment) => {
    const approval = state.detail?.approval
    if (!approval || approvalState.phase === 'pending' || approvalState.phase === 'success') return
    if (!decidedBy.trim()) {
      dispatch({ type: 'FAILURE', message: '결정자를 입력해 주세요.' })
      return
    }
    dispatch({ type: 'SUBMIT' })
    decideApprovalCanonical(approval.approval_id, {
      decision,
      decided_by: decidedBy.trim(),
      decision_comment: comment.trim() || undefined,
    }).then(
      (result) => {
        dispatch({ type: 'SUCCESS', status: result.status })
        setState((current) => ({
          ...current,
          detail: current.detail
            ? {
                ...current.detail,
                approval: current.detail.approval ? { ...current.detail.approval, ...result } : null,
                action: current.detail.action
                  ? { ...current.detail.action, approval_status: result.status }
                  : null,
              }
            : null,
        }))
        load()
      },
      (error) => {
        if (error?.response?.status === 409) {
          dispatch({ type: 'CONFLICT' })
          load()
        } else {
          dispatch({ type: 'FAILURE', message: publicErrorMessage(error, '승인 결정을 저장하지 못했습니다.') })
        }
      },
    )
  }

  if (state.phase === 'loading') return <LoadingState message="Agent 실행 상세를 불러오는 중…" />
  if (state.phase === 'missing') return <EmptyState title="해당 Agent 실행을 찾을 수 없습니다" description={runId} />
  if (state.phase === 'error') return <ErrorState title="Agent 실행 상세 오류" detail={state.error} onRetry={load} />

  const { detail, alarm } = state
  const { run, wafer, lim, docs } = view
  const listRuns = state.runs.map((item) => ({
    ...adaptRunForLegacyPage(item),
    fault_code: item.predicted_fault_code ?? null,
  }))
  const alarmEvidence = detail.evidence_items.find((item) => item.type === 'ALARM')

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[20px] font-extrabold text-ink">Agent 분석 · 승인</div>
        <div className="text-[11.5px] text-g2">자동 분석 {state.runs.length}건 · EQP_HOLD는 사람 승인 후 전송</div>
      </div>

      <div className="flex items-start gap-4">
        <RunListPanel
          runs={listRuns}
          selectedId={detail.agent_run_id}
          onSelect={(id) => navigate(`/agent-runs/${encodeURIComponent(id)}`)}
        />
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <RunHeaderCard run={run} approvalStatus={approvalState.status} />
          <HistoryTrendCard
            alarm={alarm ?? (wafer ? { sensor_id: wafer.sensor_id, wafer_no: wafer.wafer_no, chamber_id: wafer.chamber_id } : null)}
            wafer={wafer}
            lim={lim}
            loading={false}
            emptyMessage={state.trendMessage}
          />
          <RunSummaryCard
            run={run}
            repAlarm={alarm}
            lim={lim}
            action={detail.action}
            alarmHref={alarmEvidence ? evidenceHref(alarmEvidence) : null}
          />
          <div className="flex justify-end">
            <Button onClick={() => setModalOpen(true)}>근거 · 조치 상세 보기</Button>
          </div>
        </div>
      </div>

      <RunDetailModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        run={run}
        action={detail.action}
        approval={detail.approval}
        docs={docs}
        evidenceItems={detail.evidence_items}
        tools={detail.tools}
        approvalState={approvalState}
        onDecide={decide}
      />
    </div>
  )
}

export default function AgentRunPage() {
  const { runId } = useParams()
  return runId ? <AgentRunDetailPage key={runId} runId={runId} /> : <AgentLanding />
}
