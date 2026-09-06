import { STOP_LABELS, TOOL_LABELS, investigationTimelineState } from '../investigation-view.js'

const PHASE_LABELS = { SELECTED: '선택', OBSERVED: '관찰', REJECTED: '선택 거부', STOPPED: '종료' }

export default function RunInvestigationTimeline({ detail }) {
  const view = investigationTimelineState(detail)
  if (view.phase === 'hidden') return null
  return (
    <section className="rounded-xl border border-line bg-white p-5" aria-label="에이전트 조사 타임라인">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-[15px] font-extrabold text-navy">에이전트 조사 타임라인</h2>
        {typeof detail.remaining_read_calls === 'number' && <span className="text-xs text-g1">
          남은 조회 예산 {detail.remaining_read_calls} / 8
        </span>}
      </div>
      {view.phase !== 'success' ? <p className="mt-3 text-sm text-g1" role="status">{view.message}</p> : (
        <ol className="mt-4 space-y-3">
          {detail.react_trace.map((step) => (
            <li key={step.seq} className="rounded-lg border border-line p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2 font-semibold text-navy">
                <span className="font-mono">{step.seq}</span><span>{PHASE_LABELS[step.phase]}</span>
                <span>{TOOL_LABELS[step.tool] ?? '시스템'}</span>
                {step.degraded && <span className="text-tint-amber-text">수집된 근거로 가설 생성</span>}
              </div>
              {step.rationale_summary && <p className="mt-2 text-ink">{step.rationale_summary}</p>}
              {step.argument_summary && <p className="mt-1 text-xs text-g1">대상: {step.argument_summary}</p>}
              {step.observation_summary && <p className="mt-2 text-ink">관찰: {step.observation_summary}</p>}
              {step.guard_code && <p className="mt-2 text-xs text-tint-amber-text">선택 검증: {step.guard_code}</p>}
              {step.stop_reason && <p className="mt-2 text-g1">{STOP_LABELS[step.stop_reason] ?? '조사 종료'}</p>}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
