import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { decideApprovalCanonical, getAgentEvaluations, getRun, getRunsCore } from '../../../shared/api/agent.js'
import { getAlarm, searchTraces } from '../../../shared/api/detection.js'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import AlarmTracePanel from '../../../shared/components/trace/AlarmTracePanel.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { alarmTrendScope } from '../../../shared/trace/incidentTrace.js'
import {
  adaptRunForLegacyPage,
  approvalViewState,
  documentHitsOf,
  selectInitialRun,
  shouldPollAgentRun,
} from '../agent-run-view-state.js'
import AgentEvaluationPanel from '../components/AgentEvaluationPanel.jsx'
import AgentExecutionFlow from '../components/AgentExecutionFlow.jsx'
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

const legacyRunOf = (detail, alarm) => {
  const base = adaptRunForLegacyPage(detail)
  const representativeAlarm = detail.evidence_items.find((item) => item.type === 'ALARM')
  const alarmCount = detail.evidence_items.filter((item) => item.type === 'ALARM').length
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
    cause_summary: detail.prediction?.cause_summary ?? null,
    action_reason: detail.action?.reason ?? null,
  }
}

function AgentRunDetailPage({ runId }) {
  const navigate = useNavigate()
  const requestRef = useRef(null)
  const pollCountRef = useRef(0)
  const [modalOpen, setModalOpen] = useState(false)
  const [section, setSection] = useState('run')
  const [pollingEnded, setPollingEnded] = useState(false)
  const [state, setState] = useState({
    phase: 'loading',
    runs: [],
    detail: null,
    alarm: null,
    traceResponse: null,
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
      getAgentEvaluations({ signal: controller.signal }).catch(() => null),
    ]).then(
      async ([runs, detail, evaluation]) => {
        if (controller.signal.aborted) return
        if (detail == null) {
          setState({
            phase: 'missing',
            runs,
            detail: null,
            alarm: null,
            traceResponse: null,
            trendMessage: null,
            evaluation,
            error: null,
          })
          return
        }
        const alarmEvidence = detail.evidence_items.find((item) => item.type === 'ALARM')
        const alarm = alarmEvidence
          ? await getAlarm(
              alarmEvidence.alarm.alarm_id,
              alarmEvidence.alarm.source,
              { signal: controller.signal },
            ).catch(() => null)
          : null
        const traceScope = alarmTrendScope(alarm)
        const traceResponse = traceScope
          ? await searchTraces(traceScope, { signal: controller.signal }).catch(() => null)
          : null
        if (controller.signal.aborted) return
        const trendMessage = !alarmEvidence
          ? '이 실행에 대표 알람 근거가 없습니다.'
          : !alarm
            ? '현재 Detection 데이터에 이 실행의 알람이 없어 트렌드를 표시할 수 없습니다.'
            : !traceResponse
              ? 'incident trace 조회에 실패했거나 조회 식별자가 부족합니다.'
              : traceResponse.wafers?.length
                ? null
                : '이 incident의 trace 실측 데이터가 없습니다.'
        dispatch({ type: 'RESET', status: detail.approval?.status ?? detail.action?.approval_status ?? null })
        setState({
          phase: 'success',
          runs,
          detail,
          alarm,
          traceResponse,
          trendMessage,
          evaluation,
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
          traceResponse: null,
          trendMessage: null,
          evaluation: null,
          error: publicErrorMessage(error, 'Agent 실행 상세를 불러오지 못했습니다.'),
        })
      },
    )
  }, [runId])

  useEffect(() => {
    pollCountRef.current = 0
    load()
    return () => requestRef.current?.abort()
  }, [load])

  useEffect(() => {
    if (state.phase !== 'success' || !shouldPollAgentRun(state.detail)) return undefined
    if (pollCountRef.current >= 15) {
      const exhausted = window.setTimeout(() => setPollingEnded(true), 0)
      return () => window.clearTimeout(exhausted)
    }
    const timer = window.setTimeout(() => {
      pollCountRef.current += 1
      load()
    }, 2000)
    return () => window.clearTimeout(timer)
  }, [load, state.detail, state.phase])

  const view = useMemo(() => {
    if (state.phase !== 'success') return null
    const run = legacyRunOf(state.detail, state.alarm)
    const parameterId = state.alarm?.parameter_id ?? state.alarm?.sensor_id
    const wafer = (state.traceResponse?.wafers ?? []).find(
      (item) => item.sensor_id === parameterId && Number(item.wafer_no) === Number(state.alarm?.wafer_no),
    ) ?? state.traceResponse?.wafers?.[0] ?? null
    const lim = state.traceResponse?.limits?.[parameterId] ?? null
    return { run, wafer, lim, docs: documentHitsOf(state.detail.evidence_items) }
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
  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[22px] font-extrabold text-ink">Agent 분석 · 승인</div>
        <div className="flex items-center gap-3">
          <button type="button" onClick={() => setSection(section === 'run' ? 'evaluation' : 'run')} className="text-[13px] font-bold text-blue">
            {section === 'run' ? '평가 결과 보기' : '실행 상세 보기'}
          </button>
          <div className="text-[12.5px] text-g2">분석 실행 {state.runs.length}건 · EQP_HOLD는 사람 승인 후 전송</div>
        </div>
      </div>

      {section === 'evaluation' ? (
        <AgentEvaluationPanel evaluation={state.evaluation} />
      ) : <div className="flex items-start gap-4">
        <RunListPanel
          runs={listRuns}
          selectedId={detail.agent_run_id}
          onSelect={(id) => navigate(`/agent-runs/${encodeURIComponent(id)}`)}
        />
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <RunHeaderCard run={run} action={detail.action} approval={detail.approval} />
          <div className="agent-main-readable">
            <AlarmTracePanel
              alarm={alarm ?? (wafer ? { sensor_id: wafer.sensor_id, wafer_no: wafer.wafer_no, chamber_id: wafer.chamber_id } : null)}
              wafer={wafer}
              lim={lim}
              response={state.traceResponse}
              loading={false}
              emptyMessage={state.trendMessage}
              allowWaferSelection
            />
          </div>
          <AgentExecutionFlow detail={detail} alarm={alarm} />
          {pollingEnded && (
            <div className="flex items-center justify-between rounded-lg border border-tint-amber-line bg-tint-amber px-4 py-2 text-[12.5px] text-tint-amber-text">
              <span>30초 자동 갱신이 종료됐습니다. 전송 재시도는 수행하지 않았습니다.</span>
              <Button sm onClick={() => { pollCountRef.current = 0; setPollingEnded(false); load() }}>수동 새로고침</Button>
            </div>
          )}
          <RunSummaryCard
            run={run}
            detail={detail}
            repAlarm={alarm}
            wafer={wafer}
            lim={lim}
            action={detail.action}
          />
          <div className="flex justify-end">
            <Button onClick={() => setModalOpen(true)}>근거 · 조치 상세 보기</Button>
          </div>
        </div>
      </div>}

      <RunDetailModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        run={run}
        detail={detail}
        action={detail.action}
        approval={detail.approval}
        docs={docs}
        evidenceItems={detail.evidence_items}
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
