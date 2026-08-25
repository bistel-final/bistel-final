// 그래프 노드 색 — Chamber blue / Parameter sky / Alarm red / Fault amber / Action green / Document gray
const NODE_HEX = {
  Chamber: '#2563eb',
  Parameter: '#0ea5e9',
  Alarm: '#dc2626',
  Fault: '#f59e0b',
  Action: '#16a34a',
  Document: '#94a3b8',
}

function GraphDiagram({ run, docId }) {
  const nodes = [
    { type: 'Chamber', label: run.incident?.chamber_id, x: 120, y: 150 },
    { type: 'Parameter', label: run.sensor_id, x: 330, y: 78 },
    { type: 'Alarm', label: run.representative_alarm_id, x: 330, y: 222 },
    { type: 'Fault', label: run.fault_code, x: 560, y: 78 },
    { type: 'Action', label: run.recommended_action, x: 560, y: 222 },
    { type: 'Document', label: docId ?? 'Document', x: 760, y: 150 },
  ]
  const at = (t) => nodes.find((n) => n.type === t)
  const edges = [
    ['Alarm', 'Parameter', 'ON_PARAM'],
    ['Parameter', 'Chamber', 'MEASURED_ON'],
    ['Alarm', 'Chamber', 'OCCURRED_ON'],
    ['Alarm', 'Fault', 'CLASSIFIED_AS'],
    ['Fault', 'Action', 'RECOMMENDS'],
    ['Fault', 'Document', 'EVIDENCED_BY'],
  ]
  return (
    <svg viewBox="0 0 880 300" className="block w-full" fontFamily="IBM Plex Mono, monospace">
      {edges.map(([a, b, label]) => {
        const s = at(a)
        const t = at(b)
        const mx = (s.x + t.x) / 2
        const my = (s.y + t.y) / 2
        return (
          <g key={label}>
            <line x1={s.x} y1={s.y} x2={t.x} y2={t.y} stroke="var(--color-dash-line)" strokeWidth="1.4" />
            <text x={mx} y={my - 6} fontSize="8.5" fill="var(--color-g2)" textAnchor="middle">
              {label}
            </text>
          </g>
        )
      })}
      {nodes.map((n) => (
        <g key={n.type}>
          <circle cx={n.x} cy={n.y} r="24" fill={NODE_HEX[n.type]} opacity="0.14" />
          <circle cx={n.x} cy={n.y} r="24" fill="none" stroke={NODE_HEX[n.type]} strokeWidth="2" />
          <text x={n.x} y={n.y + 3.5} fontSize="9" fontWeight="700" fill={NODE_HEX[n.type]} textAnchor="middle">
            {n.type}
          </text>
          <text x={n.x} y={n.y + 42} fontSize="9.5" fontWeight="700" fill="var(--color-ink)" textAnchor="middle">
            {n.label}
          </text>
        </g>
      ))}
    </svg>
  )
}

function RunGraphEvidenceTab({ run, docId }) {
  const cypher = `MATCH (a:Alarm {alarm_id: '${run.representative_alarm_id}'})-[:ON_PARAM]->(p:Parameter)-[:MEASURED_ON]->(c:Chamber)
MATCH (a)-[:CLASSIFIED_AS]->(f:Fault)-[:RECOMMENDS]->(act:Action)
OPTIONAL MATCH (f)-[:EVIDENCED_BY]->(d:Document)
RETURN a, p, c, f, act, d`
  const paths = [
    `(${run.representative_alarm_id})-[:ON_PARAM]->(${run.sensor_id})-[:MEASURED_ON]->(${run.incident?.chamber_id})`,
    `(${run.representative_alarm_id})-[:CLASSIFIED_AS]->(${run.fault_code})-[:RECOMMENDS]->(${run.recommended_action})`,
    ...(docId ? [`(${run.fault_code})-[:EVIDENCED_BY]->(${docId})`] : []),
  ]

  return (
    <div className="flex flex-col gap-4">
      <pre className="overflow-x-auto rounded-[10px] bg-[#0f172a] px-4 py-3.5 font-mono text-[11.5px] leading-[1.7] text-[#dbeafe]">
        {cypher}
      </pre>
      <div className="rounded-[10px] border border-line p-3">
        <GraphDiagram run={run} docId={docId} />
      </div>
      <div>
        <div className="mb-1.5 text-[11px] font-bold text-g2">조회된 경로</div>
        <div className="flex flex-col gap-1 font-mono text-[11px] text-g1">
          {paths.map((p) => (
            <div key={p}>{p}</div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default RunGraphEvidenceTab
