import { DashedCard } from '../../../shared/components/ui/Card.jsx'

<<<<<<< Updated upstream
// tool_calls: {tool_call_id, call_seq, tool_name, status, latency_ms, called_at, error_msg}
// status 성공값은 'SUCCESS' 다 ('OK' 아님 — 레거시 값으로 비교하면 전부 실패로 보인다).
// decide_action·classify_fault 는 Tool 이 아니라 그래프 노드 — run.nodes 로 내려오며
// 반드시 구분선 아래 「노드」 구획에만 둔다 (계약 사항).
const SUCCESS = 'SUCCESS'
=======
const SHOWN_TOOLS = ['get_fdc_summary', 'get_equipment_context', 'search_documents', 'send_action']
const STATUS_CLASS = { SUCCESS: 'bg-dot-green', ERROR: 'bg-red', TIMEOUT: 'bg-amber' }
>>>>>>> Stashed changes

const fmtMs = (latencyMs) =>
  typeof latencyMs === 'number' ? `${latencyMs.toLocaleString('en-US')}ms` : '—'

<<<<<<< Updated upstream
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
=======
function RunToolCallsCard({ toolCalls }) {
  const tools = (toolCalls ?? [])
    .filter((tool) => SHOWN_TOOLS.includes(tool.tool_name))
    .sort((a, b) => a.call_seq - b.call_seq)
>>>>>>> Stashed changes

  return (
    <DashedCard className="p-4">
      <div className="mb-2.5 text-[12.5px] font-extrabold text-navy">Tool 호출</div>
      <div className="flex flex-col gap-[9px]">
<<<<<<< Updated upstream
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
=======
        {tools.length === 0 && <span className="font-mono text-[11px] text-g1">Tool 호출 기록 없음</span>}
        {tools.map((tool) => (
          <span key={tool.tool_call_id ?? `${tool.call_seq}-${tool.tool_name}`} className="flex items-center gap-2">
            <span
              className={`h-[7px] w-[7px] flex-none rounded-full ${STATUS_CLASS[tool.status] ?? 'bg-g2'}`}
              title={tool.status}
            />
            <span className="w-4 font-mono text-[10px] text-g2">{tool.call_seq}</span>
            <span className="font-mono text-[11.5px] text-ink">{tool.tool_name}</span>
            <span className="ml-auto font-mono text-[10.5px] text-g2">{fmtMs(tool.latency_ms)}</span>
          </span>
        ))}
      </div>
>>>>>>> Stashed changes
    </DashedCard>
  )
}

export default RunToolCallsCard
