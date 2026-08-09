import StatusBadge from '../../../shared/components/StatusBadge.jsx'

const DASH = '—'

// 판정 카드 — fault 분류와 confidence(Agent 판정 신뢰도)를 한 화면에 묶는다.
// confidence는 anomaly_score(이상 탐지 참고 지표)와 다른 축이므로 라벨을 분명히 나눈다.
function RunVerdictCard({ run, anomaly }) {
  const fault = run.fault
  const confidence = typeof run.confidence === 'number' ? run.confidence : null

  return (
    <div className="rounded-xl border border-line bg-white px-[18px] py-4 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-sm font-extrabold text-navy">판정</span>
        <StatusBadge tone="info" mono>
          classify_fault
        </StatusBadge>
      </div>

      <div className="flex items-start gap-3.5">
        <span className="flex h-[52px] min-w-[70px] flex-none items-center justify-center rounded-xl bg-navy px-3 font-mono text-[24px] font-extrabold tracking-[-.5px] text-white">
          {run.fault_code || DASH}
        </span>
        <div className="flex min-w-0 flex-col gap-1 pt-1">
          <div className="text-[16.5px] font-extrabold text-navy">{fault?.name ?? '실측 미제공'}</div>
          <div className="text-[13px] font-semibold leading-[1.55] text-slate">
            {fault?.cause ?? '원인 문구 실측 미제공'}
          </div>
        </div>
      </div>

      <div className="mt-4 rounded-[10px] border border-line bg-page px-3.5 py-3">
        <div className="flex items-end gap-2.5">
          <span className="font-mono text-[12px] font-extrabold text-slate">confidence</span>
          <span className="font-mono text-[26px] font-extrabold leading-none text-brand">
            {confidence === null ? DASH : confidence.toFixed(2)}
          </span>
          <span className="pb-0.5 text-[12px] font-bold text-slate-light">Agent 판정 신뢰도</span>
        </div>
        <div className="mt-2 h-2 overflow-hidden rounded bg-line-soft">
          <div
            className="h-full rounded bg-brand origin-left animate-[om-grow_.5s_ease-out]"
            style={{ width: `${(confidence ?? 0) * 100}%` }}
          />
        </div>
        <div className="mt-2.5 flex items-center gap-2 border-t border-line pt-2.5">
          <span className="font-mono text-[11.5px] font-bold text-slate-light">anomaly_score</span>
          <span className="font-mono text-[13px] font-extrabold text-ink">
            {anomaly ? anomaly.score.toFixed(2) : DASH}
          </span>
          {anomaly && (
            <span className="font-mono text-[11.5px] font-semibold text-slate-light">
              (임계 {anomaly.threshold.toFixed(2)})
            </span>
          )}
          <span className="ml-auto text-[11.5px] font-bold text-slate-light">
            confidence와 다른 지표 — 판정에는 쓰지 않는 참고값
          </span>
        </div>
      </div>
    </div>
  )
}

export default RunVerdictCard
