import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { decideApproval, getAction, getApprovals, getRun, getRuns } from '../../../shared/api/agent.js'
import { getAlarms, searchTraces } from '../../../shared/api/detection.js'
import { searchDocuments } from '../../../shared/api/knowledge.js'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { HistoryTrendCard } from '../../detection/components/HistoryTrendChart.jsx'
import RunListPanel from '../components/RunListPanel.jsx'
import RunHeaderCard from '../components/RunHeaderCard.jsx'
import RunSummaryCard from '../components/RunSummaryCard.jsx'
import RunDetailModal from '../components/RunDetailModal.jsx'

// Agent 분석 · 승인 — 라이트 시안 3번 (채팅 없음)
// 좌 286px 자동 분석 실행 리스트 + 우측 스택(헤더 / 파라미터 트렌드 / 알람 요약) + [근거 · 조치 상세 보기] 모달
const WIDE = 200

// RAG 근거 검색어 — fault 별 대표 질의로 매핑한다.
// TODO(api): 실행별 근거 chunk 반환 API 정의 시 run 기반 조회로 교체
const DOC_QUERY = {
  RFM: '반사파가 올라가면 무슨 문제인가',
  FOC: '포커스가 벗어나면 CD가 어떻게 되나',
}
const docQueryOf = (run) =>
  DOC_QUERY[run.fault_code] ?? (run.recommended_action === 'EQP_HOLD' ? '장비를 세우려면 승인이 필요한가' : run.cause_summary)

function AgentRunPage() {
  const { runId } = useParams()
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [decided, setDecided] = useState(null)
  const [deciding, setDeciding] = useState(false)

  const load = useCallback(() => {
    Promise.all([getRuns({ page: 1, size: 50 }), getRun(runId)])
      .then(([runPage, run]) => {
        if (!run) return { runs: runPage.items ?? [], run: null }
        return Promise.all([
          getAction(run.action_id),
          getApprovals(),
          getAlarms({ chamber_id: run.incident?.chamber_id, page: 1, size: WIDE }),
          searchTraces({
            chamber_id: run.incident?.chamber_id,
            sensor_ids: [run.sensor_id],
            lot_id: run.incident?.lot_id,
          }),
          // RAG 근거는 실서버 호출 — 실패해도 페이지는 띄운다 (모달 RAG 탭만 빈 상태)
          searchDocuments({ query: docQueryOf(run), top_k: 3 }).catch(() => ({ query: '', hits: [] })),
        ]).then(([action, approvals, alarmPage, trace, docs]) => ({
          runs: runPage.items ?? [],
          run,
          action,
          approvals: approvals.items ?? [],
          alarms: alarmPage.items ?? [],
          trace,
          docs,
        }))
      })
      .then((d) => {
        setDecided(null)
        setData(d)
      })
      .catch((e) => setError(e.message))
  }, [runId])
  useEffect(() => {
    load()
  }, [load])

  const derived = useMemo(() => {
    if (!data?.run) return null
    const { run, alarms, approvals, trace } = data
    const repAlarm = alarms.find((a) => a.alarm_id === run.representative_alarm_id) ?? null
    // 트렌드는 대표 알람 웨이퍼 우선, 없으면 조회된 첫 웨이퍼
    // wafer_no 는 응답에 따라 문자열/숫자가 섞인다 — 숫자로 정규화해 비교
    const wafer =
      (repAlarm && (trace.wafers ?? []).find((w) => Number(w.wafer_no) === Number(repAlarm.wafer_no))) ??
      trace.wafers?.[0] ??
      null
    return {
      repAlarm,
      wafer,
      lim: trace.limits?.[run.sensor_id] ?? null,
      approval: approvals.find((p) => p.agent_run_id === run.agent_run_id) ?? null,
    }
  }, [data])

  const onDecide = (decision) => {
    const approvalId = derived?.approval?.approval_id
    if (!approvalId) return
    setDeciding(true)
    decideApproval(approvalId, { decision, decided_by: 'bang' })
      .then((res) => setDecided(res.approval_status))
      .catch((e) => setError(e.message))
      .finally(() => setDeciding(false))
  }

  const retry = () => {
    setError(null)
    setData(null)
    load()
  }

  if (error) return <ErrorState title="Agent 실행을 불러오지 못했습니다" detail={error} onRetry={retry} />
  if (!data) return <LoadingState message="Agent 실행 결과를 불러오는 중…" />
  if (!data.run)
    return (
      <EmptyState title="해당 Agent 실행을 찾을 수 없습니다" description={`agent_run_id ${runId} 에 대한 실행 기록이 없습니다.`} />
    )

  const { run, action, docs } = data
  const { repAlarm, wafer, lim, approval } = derived
  const approvalStatus = decided ?? action?.approval_status

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[20px] font-extrabold text-ink">Agent 분석 · 승인</div>
        <div className="text-[11.5px] text-g2">자동 분석 {data.runs.length}건 · EQP_HOLD 는 사람 승인 후 전송</div>
      </div>

      <div className="flex items-start gap-4">
        <RunListPanel runs={data.runs} selectedId={run.agent_run_id} onSelect={(id) => navigate(`/agent-runs/${id}`)} />

        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <RunHeaderCard run={run} approvalStatus={approvalStatus} />

          <HistoryTrendCard
            alarm={
              repAlarm ??
              (wafer ? { sensor_id: wafer.sensor_id, wafer_no: wafer.wafer_no, chamber_id: wafer.chamber_id } : null)
            }
            wafer={wafer}
            lim={lim}
            loading={false}
          />

          <RunSummaryCard run={run} repAlarm={repAlarm} lim={lim} action={action} />

          <div className="flex justify-end">
            <Button onClick={() => setModalOpen(true)}>근거 · 조치 상세 보기</Button>
          </div>
        </div>
      </div>

      <RunDetailModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        run={run}
        action={action}
        approval={approval}
        docs={docs}
        decided={decided}
        onDecide={onDecide}
        deciding={deciding}
      />
    </div>
  )
}

export default AgentRunPage
