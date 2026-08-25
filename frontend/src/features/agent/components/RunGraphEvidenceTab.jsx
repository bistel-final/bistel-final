import { useCallback, useEffect, useMemo, useState } from 'react'
import { getChamberRelations } from '../../../shared/api/knowledge.js'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import LoadingState from '../../../shared/components/LoadingState.jsx'

const NODE_HEX = {
  Area: '#0f766e',
  Chamber: '#2563eb',
  Equipment: '#16a34a',
  EquipmentModel: '#7c3aed',
  Parameter: '#0ea5e9',
  ProcessStep: '#f59e0b',
}

const LABEL_TEXT = {
  Area: 'AREA',
  Chamber: 'CHAMBER',
  Equipment: 'EQP',
  EquipmentModel: 'MODEL',
  Parameter: 'PARAM',
  ProcessStep: 'STEP',
}

const GRAPH_CENTER = { x: 410, y: 185 }

const RELATION_TEXT = {
  IN_AREA: '공정 영역',
  MEASURED_ON: '측정 챔버',
  NEXT_STEP: '다음 공정',
  OF_MODEL: '설비 모델',
  PART_OF: '소속 설비',
  PERFORMS: '수행 공정',
}

const sortById = (a, b) => String(a.id).localeCompare(String(b.id))

const nodeId = (label, businessId) => `${label}:${businessId}`

const graphNode = (label, businessId, displayName = businessId, properties = {}) => ({
  id: nodeId(label, businessId),
  label,
  business_id: businessId,
  display_name: displayName,
  properties,
})

const graphRelationship = (type, source, target, displayType = type) => ({
  id: `${type}:${source}->${target}`,
  type,
  display_type: displayType,
  source,
  target,
})

function normalizeProjection(raw) {
  if (!raw) return null
  if (Array.isArray(raw.nodes) && Array.isArray(raw.relationships)) return raw

  // legacy mock DTO를 화면 표시용 projection으로 변환하는 경로다.
  // 여기서 만드는 display_type은 Neo4j 실제 relationship type이 아니다.
  const nodes = []
  const relationships = []
  const pushNode = (node) => {
    if (node?.business_id && !nodes.some((item) => item.id === node.id)) nodes.push(node)
  }
  const pushRel = (type, source, target, displayType) => {
    if (source && target) relationships.push(graphRelationship(type, source, target, displayType))
  }

  const chamber = raw.chamber
  const equipment = raw.equipment
  const area = raw.area
  const step = raw.step

  const chamberId = chamber?.chamber_id
  const equipmentId = equipment?.equipment_id
  const areaId = area?.area_id
  const stepId = step?.step_id

  pushNode(chamberId && graphNode('Chamber', chamberId, chamberId, chamber))
  pushNode(equipmentId && graphNode('Equipment', equipmentId, equipment.equipment_name ?? equipmentId, equipment))
  pushNode(areaId && graphNode('Area', areaId, area.area_name ?? areaId, area))
  pushNode(stepId && graphNode('ProcessStep', stepId, step.step_name ?? stepId, step))

  if (equipmentId && chamberId) {
    pushRel('mock-has-chamber', nodeId('Equipment', equipmentId), nodeId('Chamber', chamberId), '장비 챔버')
  }
  if (equipmentId && areaId) pushRel('mock-area', nodeId('Equipment', equipmentId), nodeId('Area', areaId), '공정 영역')
  if (equipmentId && stepId) {
    pushRel('mock-step', nodeId('Equipment', equipmentId), nodeId('ProcessStep', stepId), '공정 단계')
  }

  for (const sibling of raw.sibling_chambers ?? []) {
    pushNode(graphNode('Chamber', sibling.chamber_id, sibling.chamber_id, sibling))
    pushRel('mock-sibling-chamber', nodeId('Chamber', chamberId), nodeId('Chamber', sibling.chamber_id), '같은 설비 챔버')
  }
  for (const upstream of raw.upstream ?? []) {
    pushNode(graphNode('Equipment', upstream.equipment_id, upstream.equipment_name ?? upstream.equipment_id, upstream))
    pushRel('mock-upstream-equipment', nodeId('Equipment', upstream.equipment_id), nodeId('Equipment', equipmentId), '상류 설비')
  }
  for (const downstream of raw.downstream ?? []) {
    pushNode(graphNode('Equipment', downstream.equipment_id, downstream.equipment_name ?? downstream.equipment_id, downstream))
    pushRel('mock-downstream-equipment', nodeId('Equipment', equipmentId), nodeId('Equipment', downstream.equipment_id), '하류 설비')
  }

  return {
    root_node_id: chamberId ? nodeId('Chamber', chamberId) : nodes[0]?.id,
    nodes,
    relationships,
    graph_revision: raw.graph_revision ?? 'mock',
  }
}

function spreadItems(items, slot) {
  const sorted = [...items].sort(sortById)
  const offset = ((sorted.length - 1) * slot.gap) / 2
  return sorted.map((node, index) => ({
    ...node,
    x: slot.axis === 'x' ? slot.x + index * slot.gap - offset : slot.x,
    y: slot.axis === 'x' ? slot.y : slot.y + index * slot.gap - offset,
    isRoot: false,
  }))
}

function layoutNodes(nodes, rootNodeId) {
  const root = nodes.find((node) => node.id === rootNodeId)
  const groups = nodes.reduce((acc, node) => {
    if (node.id === rootNodeId) return acc
    const label = node.label ?? 'Unknown'
    acc[label] = [...(acc[label] ?? []), node]
    return acc
  }, {})

  const positioned = new Map()
  if (root) positioned.set(root.id, { ...root, ...GRAPH_CENTER, isRoot: true })

  const slots = {
    Area: { x: 560, y: 78, axis: 'x', gap: 120 },
    Chamber: { x: 410, y: 76, axis: 'x', gap: 130 },
    Equipment: { x: 245, y: 185, axis: 'y', gap: 76 },
    EquipmentModel: { x: 92, y: 185, axis: 'y', gap: 76 },
    Parameter: { x: 410, y: 310, axis: 'x', gap: 116 },
    ProcessStep: { x: 590, y: 185, axis: 'y', gap: 76 },
  }

  for (const [label, group] of Object.entries(groups)) {
    const slot = slots[label] ?? { x: 410, y: 185, axis: 'y', gap: 76 }
    for (const node of spreadItems(group, slot)) positioned.set(node.id, node)
  }

  return positioned
}

function nodeTitle(node) {
  return node.display_name && node.display_name !== node.business_id ? node.display_name : node.business_id
}

function relationTitle(rel) {
  return rel.display_type ?? RELATION_TEXT[rel.type] ?? rel.type
}

function EvidenceGraph({ graph }) {
  const positioned = useMemo(() => layoutNodes(graph.nodes, graph.root_node_id), [graph])
  const nodes = [...positioned.values()]
  const relationships = graph.relationships.filter((rel) => positioned.has(rel.source) && positioned.has(rel.target))

  return (
    <svg viewBox="0 0 820 370" className="block w-full" fontFamily="IBM Plex Mono, monospace">
      <defs>
        <marker id="graph-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M 0 0 L 8 4 L 0 8 z" fill="var(--color-dash-line)" />
        </marker>
      </defs>
      {relationships.map((rel) => {
        const source = positioned.get(rel.source)
        const target = positioned.get(rel.target)
        const midX = (source.x + target.x) / 2
        const midY = (source.y + target.y) / 2
        return (
          <g key={rel.id}>
            <line
              x1={source.x}
              y1={source.y}
              x2={target.x}
              y2={target.y}
              stroke="var(--color-dash-line)"
              strokeWidth="1.4"
              markerEnd="url(#graph-arrow)"
            />
            <text x={midX} y={midY - 6} fontSize="8.5" fill="var(--color-g2)" textAnchor="middle">
              {relationTitle(rel)}
            </text>
          </g>
        )
      })}
      {nodes.map((node) => {
        const color = NODE_HEX[node.label] ?? '#64748b'
        return (
          <g key={node.id}>
            <rect
              x={node.x - 55}
              y={node.y - 24}
              width="110"
              height="48"
              rx="10"
              fill={color}
              opacity={node.isRoot ? '0.18' : '0.1'}
            />
            <rect
              x={node.x - 55}
              y={node.y - 24}
              width="110"
              height="48"
              rx="10"
              fill="none"
              stroke={color}
              strokeWidth={node.isRoot ? '2.4' : '1.6'}
            />
            <text x={node.x} y={node.y - 4} fontSize="8.5" fontWeight="700" fill={color} textAnchor="middle">
              {LABEL_TEXT[node.label] ?? node.label}
            </text>
            <text x={node.x} y={node.y + 11} fontSize="9.5" fontWeight="700" fill="var(--color-ink)" textAnchor="middle">
              {node.business_id}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

function RelationList({ graph }) {
  const root = graph.nodes.find((node) => node.id === graph.root_node_id)
  const valuesByLabel = (label, filter = () => true) =>
    graph.nodes
      .filter((node) => node.label === label && filter(node))
      .map((node) => node.business_id)
      .filter(Boolean)
      .sort((a, b) => String(a).localeCompare(String(b)))

  const rows = [
    ['기준 챔버', root?.business_id],
    ['설비', valuesByLabel('Equipment').join(', ')],
    ['형제 챔버', valuesByLabel('Chamber', (node) => node.id !== graph.root_node_id).join(', ')],
    ['모델', valuesByLabel('EquipmentModel').join(', ')],
    ['공정 단계', valuesByLabel('ProcessStep').join(', ')],
    ['공정 영역', valuesByLabel('Area').join(', ')],
    ['파라미터', valuesByLabel('Parameter').join(', ')],
  ].filter(([, value]) => value)

  return (
    <div className="grid gap-2 md:grid-cols-2">
      {rows.map(([label, value]) => (
        <div key={label} className="rounded-lg border border-line bg-white px-3 py-2 text-[12px]">
          <span className="font-bold text-navy">{label}: </span>
          <span className="font-mono font-semibold text-ink">{value}</span>
        </div>
      ))}
    </div>
  )
}

function NodeSummary({ graph }) {
  const root = graph.nodes.find((node) => node.id === graph.root_node_id)
  const counts = graph.nodes.reduce((acc, node) => {
    acc[node.label] = (acc[node.label] ?? 0) + 1
    return acc
  }, {})

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-[10px] border border-line bg-soft px-3.5 py-3">
      <span className="font-mono text-[11.5px] font-extrabold text-navy">{root ? nodeTitle(root) : graph.root_node_id}</span>
      <span className="text-[11.5px] text-g2">관계 {graph.relationships.length}건</span>
      {Object.entries(counts).map(([label, count]) => (
        <span key={label} className="rounded-md bg-white px-2 py-1 font-mono text-[10.5px] font-bold text-g1">
          {LABEL_TEXT[label] ?? label} {count}
        </span>
      ))}
      <span className="ml-auto font-mono text-[10.5px] text-faint">rev {graph.graph_revision}</span>
    </div>
  )
}

function RunGraphEvidenceTab({ run }) {
  const chamberId = run.incident?.chamber_id
  const [state, setState] = useState({ chamberId: null, status: 'idle', graph: null, error: null })

  const load = useCallback((nextChamberId) => {
    getChamberRelations(nextChamberId)
      .then((response) => {
        const graph = normalizeProjection(response)
        setState({ chamberId: nextChamberId, status: graph ? 'success' : 'empty', graph, error: null })
      })
      .catch((error) => setState({ chamberId: nextChamberId, status: 'error', graph: null, error: error.message }))
  }, [])

  useEffect(() => {
    if (chamberId) load(chamberId)
  }, [chamberId, load])

  if (!chamberId) {
    return <EmptyState title="그래프 근거가 없습니다" description="챔버 정보가 없습니다" />
  }
  if (state.chamberId !== chamberId || state.status === 'idle') {
    return <LoadingState message="그래프 근거를 불러오는 중…" />
  }
  if (state.status === 'error') {
    return <ErrorState title="그래프 근거를 불러오지 못했습니다" detail={state.error} onRetry={() => load(chamberId)} />
  }
  if (!state.graph || state.graph.nodes.length === 0) {
    return <EmptyState title="그래프 근거가 없습니다" description={chamberId} />
  }

  return (
    <div className="flex flex-col gap-4">
      <NodeSummary graph={state.graph} />
      <div className="rounded-[10px] border border-line bg-white p-3">
        <EvidenceGraph graph={state.graph} />
      </div>
      <div>
        <div className="mb-2 text-[11px] font-bold text-g2">근거 요약</div>
        <RelationList graph={state.graph} />
      </div>
    </div>
  )
}

export default RunGraphEvidenceTab
