// 「승인하면」 무엇이 바뀌는지 4줄로 못 박는다 — 결정 전에 결과를 예측할 수 있게
const LINES = [
  'approval PENDING → APPROVED',
  'action 전송 WAITING',
  'run WAITING_APPROVAL → RUNNING',
  '커밋 뒤 같은 thread_id 로 재개',
]

function RunTransitionCard({ threadId }) {
  return (
    <div className="rounded-xl border border-line bg-white px-[18px] py-4 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-sm font-extrabold text-navy">승인하면</span>
        <span className="text-[11.5px] font-bold text-slate-light">상태 전이 미리보기</span>
      </div>
      <div className="flex flex-col">
        {LINES.map((line, i) => (
          <div
            key={line}
            className="flex items-center gap-2.5 border-b border-line-soft py-2 last:border-b-0"
          >
            <span className="flex h-[19px] w-[19px] flex-none items-center justify-center rounded-md bg-line-soft font-mono text-[11px] font-extrabold text-brand">
              {i + 1}
            </span>
            <span className="font-mono text-[12.5px] font-extrabold text-ink">{line}</span>
            {i === LINES.length - 1 && threadId && (
              <span className="ml-auto font-mono text-[11.5px] font-bold text-slate-light">{threadId}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

export default RunTransitionCard
