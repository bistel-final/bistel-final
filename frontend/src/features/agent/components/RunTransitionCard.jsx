// 「승인하면」 무엇이 바뀌는지 4줄로 못 박는다 — 시안 04 문구 그대로 (변형 금지)
const LINES = [
  'approval PENDING → APPROVED',
  'action 승인 APPROVED · 전송 WAITING',
  'agent_run WAITING_APPROVAL → RUNNING',
  '커밋 뒤 같은 thread_id 로 재개',
]

function RunTransitionCard() {
  return (
    <div className="rounded-lg border border-line bg-soft p-4">
      <div className="mb-2.5 text-[12.5px] font-extrabold text-navy">승인하면</div>
      <div className="flex flex-col gap-[7px] font-mono text-[11px] text-ink">
        {LINES.map((line) => (
          <span key={line}>{line}</span>
        ))}
      </div>
    </div>
  )
}

export default RunTransitionCard
