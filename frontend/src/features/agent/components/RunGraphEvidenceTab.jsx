import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { getChamberRelationsCore } from '../../../shared/api/knowledge.js'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import OntologyGraphCanvas from '../../../shared/components/ontology/OntologyGraphCanvas.jsx'
import {
  ONTOLOGY_NODE_META,
  hasDisplayableRelationships,
  normalizeOntologyGraph,
} from '../../../shared/graph/ontology-graph.js'
import { evidenceHref } from '../agent-run-view-state.js'

const GRAPH_ERROR = '그래프 조회 서비스가 잠시 준비되지 않았습니다.'

function GraphEvidenceLinks({ items, chamberId }) {
  if (items.length === 0) return null
  return (
    <div className="flex flex-col gap-2">
      {items.map((item) => (
        <div key={item.source_id} className="flex items-center justify-between gap-3 rounded-lg border border-tint-blue-line bg-tint-blue px-3.5 py-3">
          <span className="min-w-0">
            <span className="block truncate text-[12px] font-bold text-ink">{item.title}</span>
            <span className="block truncate font-mono text-[10.5px] text-g2">
              {item.relation_id} · rev {item.graph_revision?.slice(0, 10) ?? '미제공'}…
            </span>
          </span>
          <Link to={evidenceHref(item, { chamberId })} className="flex-none text-[12px] font-bold text-blue">
            온톨로지에서 보기 →
          </Link>
        </div>
      ))}
    </div>
  )
}

function GraphSummary({ graph, selectedNode }) {
  const root = graph.nodes.find((node) => node.id === graph.root_node_id)
  const counts = graph.nodes.reduce((result, node) => {
    result[node.label] = (result[node.label] ?? 0) + 1
    return result
  }, {})
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-[10px] border border-line bg-soft px-3.5 py-3">
      <span className="font-mono text-[11.5px] font-extrabold text-navy">
        {root?.display_name ?? graph.root_node_id}
      </span>
      <span className="text-[11.5px] text-g2">관계 {graph.relationships.length}건</span>
      {Object.entries(counts).map(([label, count]) => (
        <span key={label} className="rounded-md bg-white px-2 py-1 font-mono text-[10.5px] font-bold text-g1">
          {ONTOLOGY_NODE_META[label]?.shortLabel ?? label} {count}
        </span>
      ))}
      <span className="ml-auto font-mono text-[10.5px] text-faint">rev {graph.graph_revision ?? '미제공'}</span>
      {selectedNode && (
        <span className="w-full border-t border-cell-line pt-2 text-[11px] font-bold text-blue">
          선택 포커스 · {ONTOLOGY_NODE_META[selectedNode.label]?.shortLabel ?? selectedNode.label} ·{' '}
          <span className="font-mono">{selectedNode.display_name}</span>
        </span>
      )}
    </div>
  )
}

function RelationSummary({ graph }) {
  const valuesByLabel = (label, filter = () => true) =>
    graph.nodes
      .filter((node) => node.label === label && filter(node))
      .map((node) => node.business_id)
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b))

  const rows = [
    ['기준 챔버', valuesByLabel('Chamber', (node) => node.id === graph.root_node_id).join(', ')],
    ['설비', valuesByLabel('Equipment').join(', ')],
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

function RunGraphEvidenceTab({ run, evidenceItems = [] }) {
  const chamberId = run.incident?.chamber_id
  const graphItems = evidenceItems.filter((item) => item.type === 'GRAPH')
  const focusedRelationIds = useMemo(
    () => new Set(graphItems.map((item) => item.relation_id).filter(Boolean)),
    [graphItems],
  )
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState({ chamberId: null, status: 'idle', graph: null })
  const [selectedNode, setSelectedNode] = useState(null)

  useEffect(() => {
    if (!chamberId) return undefined
    let active = true
    getChamberRelationsCore(chamberId).then(
      (response) => {
        if (!active) return
        const graph = normalizeOntologyGraph(response)
        setState({ chamberId, status: hasDisplayableRelationships(graph) ? 'success' : 'empty', graph })
      },
      () => {
        if (active) setState({ chamberId, status: 'error', graph: null })
      },
    )
    return () => {
      active = false
    }
  }, [attempt, chamberId])

  if (!chamberId) {
    return <EmptyState title="그래프 근거가 없습니다" description="챔버 정보가 없습니다" />
  }
  if (state.chamberId !== chamberId || ['idle', 'loading'].includes(state.status)) {
    return <LoadingState message="그래프 근거를 불러오는 중…" />
  }
  if (state.status === 'error') {
    return (
      <div className="flex flex-col gap-4">
        <GraphEvidenceLinks items={graphItems} chamberId={chamberId} />
        <ErrorState
          title="그래프 근거를 불러오지 못했습니다"
          detail={GRAPH_ERROR}
          onRetry={() => {
            setState({ chamberId, status: 'loading', graph: null })
            setAttempt((value) => value + 1)
          }}
        />
      </div>
    )
  }
  if (state.status === 'empty' || !state.graph) {
    return (
      <div className="flex flex-col gap-4">
        <GraphEvidenceLinks items={graphItems} chamberId={chamberId} />
        <EmptyState title="표시 가능한 그래프 관계가 없습니다" description={chamberId} />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <GraphEvidenceLinks items={graphItems} chamberId={chamberId} />
      <GraphSummary graph={state.graph} selectedNode={selectedNode} />
      <OntologyGraphCanvas
        graph={state.graph}
        focusedRelationIds={focusedRelationIds}
        selectedNodeId={selectedNode?.id ?? null}
        onSelectNode={setSelectedNode}
      />
      <div>
        <div className="mb-2 text-[11px] font-bold text-g2">근거 요약</div>
        <RelationSummary graph={state.graph} />
      </div>
    </div>
  )
}

export default RunGraphEvidenceTab
