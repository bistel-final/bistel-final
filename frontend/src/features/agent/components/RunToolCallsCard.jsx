import { DashedCard } from '../../../shared/components/ui/Card.jsx'

// tool_calls: {tool_call_id, call_seq, tool_name, status, latency_ms, called_at, error_msg}
// status 성공값은 'SUCCESS' 다 ('OK' 아님 — 레거시 값으로 비교하면 전부 실패로 보인다).
// decide_action·classify_fault 는 Tool 이 아니라 그래프 노드 — run.nodes 로 내려오며
// 반드시 구분선 아래 「노드」 구획에만 둔다 (계약 사항).
const SUCCESS = 'SUCCESS'

// 실측 ms 만 표기 — 없으면 "—" (창작 금지)
const fmtMs = (ms) => (typeof ms === 'number' ? `${ms.toLocaleString('en-US')}ms` : '—')

function Row({ name, ms, ok = true, tool = true }) {
  return (
    <span className="flex items-center gap-2">
      <span className={`h-[7px] w-[7px] flex-none rounded-full ${!tool ? 'bg-g2' : ok ? 'bg-dot-green' : 'bg-red'}`} />
      <span className="font-mono text-[11.5px] text-ink">{name}</span>
      <span className="ml-auto font-mono text-[10.5px] text-g2">{fmtMs(ms)}</span>
    </span>
  )
}

function RunToolCallsCard({ toolCalls, nodes }) {
  const tools = [...(toolCalls ?? [])].sort((a, b) => a.call_seq - b.call_seq)
  // node 는 latency 를 응답하지 않는다 — ms 는 "—"
  const nodeRows = [...(nodes ?? [])].sort((a, b) => a.node_seq - b.node_seq)

  return (
    <DashedCard className="p-4">
      <div className="mb-2.5 text-[12.5px] font-extrabold text-navy">Tool 호출</div>
      <div className="flex flex-col gap-[9px]">
        {tools.length === 0 && <span className="font-mono text-[11px] text-g1">Tool 호출 실측 미제공</span>}
        {tools.map((t) => (
          <Row key={t.tool_call_id ?? t.call_seq} name={t.tool_name} ms={t.latency_ms} ok={t.status === SUCCESS} />
        ))}
      </div>
      <div className="my-3 border-t border-line" />
      <div className="mb-2 text-[10.5px] text-g1">노드 — Tool 아님</div>
      <div className="flex flex-col gap-[9px]">
        {nodeRows.length === 0 && <span className="font-mono text-[11px] text-g1">노드 실측 미제공</span>}
        {nodeRows.map((n) => (
          <Row key={n.node_seq ?? n.node_name} name={n.node_name} tool={false} />
        ))}
      </div>
    </DashedCard>
  )
}

export default RunToolCallsCard
