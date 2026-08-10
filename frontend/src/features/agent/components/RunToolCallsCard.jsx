import { DashedCard } from '../../../shared/components/ui/Card.jsx'

// 이 화면에서 노출하는 Tool 은 3종 — send_action 은 승인 후 전송 단계라 목록에 없다.
// decide_action 은 Tool 이 아니라 그래프 노드 — 반드시 구분선 아래 「노드」 구획에만 둔다 (계약 사항).
const SHOWN_TOOLS = ['get_fdc_summary', 'get_equipment_context', 'search_documents']

// 실측 ms 만 표기 — 없으면 "—" (창작 금지)
const fmtMs = (ms) => (typeof ms === 'number' ? `${ms.toLocaleString('en-US')}ms` : '—')

function Row({ name, ms, tool = true }) {
  return (
    <span className="flex items-center gap-2">
      <span className={`h-[7px] w-[7px] flex-none rounded-full ${tool ? 'bg-dot-green' : 'bg-g2'}`} />
      <span className="font-mono text-[11.5px] text-ink">{name}</span>
      <span className="ml-auto font-mono text-[10.5px] text-g2">{fmtMs(ms)}</span>
    </span>
  )
}

function RunToolCallsCard({ toolCalls, nodes }) {
  const tools = (toolCalls ?? []).filter((t) => SHOWN_TOOLS.includes(t.name)).sort((a, b) => a.seq - b.seq)
  const decideNode = (nodes ?? []).find((n) => n.name === 'decide_action') ?? null

  return (
    <DashedCard className="p-4">
      <div className="mb-2.5 text-[12.5px] font-extrabold text-navy">Tool 호출</div>
      <div className="flex flex-col gap-[9px]">
        {tools.length === 0 && <span className="font-mono text-[11px] text-g1">Tool 호출 실측 미제공</span>}
        {tools.map((t) => (
          <Row key={t.name} name={t.name} ms={t.ms} />
        ))}
      </div>
      <div className="my-3 border-t border-line" />
      <div className="mb-2 text-[10.5px] text-g1">노드 — Tool 아님</div>
      {decideNode ? (
        <Row name={decideNode.name} ms={decideNode.ms} tool={false} />
      ) : (
        <span className="font-mono text-[11px] text-g1">decide_action 노드 실측 미제공</span>
      )}
    </DashedCard>
  )
}

export default RunToolCallsCard
