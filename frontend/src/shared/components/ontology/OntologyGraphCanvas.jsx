import { useEffect, useMemo } from 'react'
import { Background, Controls, MarkerType, Position, ReactFlow, useReactFlow } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import {
  ONTOLOGY_NODE_META,
  ONTOLOGY_RELATION_LABELS,
  ONTOLOGY_REVERSED_RELATION_LABELS,
  connectedRelationIds,
  layoutOntologyNodes,
  normalizeOntologyGraph,
  orientOntologyRelationships,
} from '../../graph/ontology-graph.js'

const nodeStyle = (node, isRoot, isSelected, isDimmed, dimOpacity) => {
  const meta = ONTOLOGY_NODE_META[node.label]
  const isEmphasized = isRoot || isSelected
  return {
    width: 150,
    minHeight: 58,
    border: `${isSelected ? 3 : isRoot ? 2.5 : 1.5}px solid ${meta.color}`,
    borderRadius: 12,
    background: isEmphasized ? `${meta.color}22` : '#ffffff',
    boxShadow: isSelected
      ? `0 0 0 6px ${meta.color}22, 0 8px 20px rgba(15, 23, 42, 0.12)`
      : isRoot
        ? `0 0 0 4px ${meta.color}16`
        : '0 4px 12px rgba(15, 23, 42, 0.06)',
    color: '#0f172a',
    padding: '9px 12px',
    opacity: isDimmed ? dimOpacity : 1,
    transition: 'opacity 160ms ease, box-shadow 160ms ease',
  }
}

const nodeLabel = (node) => {
  const meta = ONTOLOGY_NODE_META[node.label]
  return (
    <div className="min-w-0 text-center">
      <div className="text-[9px] font-extrabold tracking-[.08em]" style={{ color: meta.color }}>
        {meta.shortLabel}
      </div>
      <div className="mt-1 truncate font-mono text-[11px] font-extrabold" title={node.display_name}>
        {node.display_name}
      </div>
    </div>
  )
}

function ViewportFocus({ active, nodeIds }) {
  const { fitView } = useReactFlow()
  const nodeKey = nodeIds.join('|')
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      fitView({
        nodes: active ? nodeIds.map((id) => ({ id })) : undefined,
        padding: active ? 0.65 : 0.14,
        minZoom: 0.38,
        maxZoom: active ? 1.05 : 1.2,
        duration: 320,
      })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [active, fitView, nodeIds, nodeKey])
  return null
}

function OntologyGraphCanvas({
  graph,
  focusedRelationIds = new Set(),
  selectedNodeId = null,
  onSelectNode = null,
  viewport = 'compact',
}) {
  const normalized = useMemo(() => normalizeOntologyGraph(graph), [graph])
  const focused = useMemo(
    () => (focusedRelationIds instanceof Set ? focusedRelationIds : new Set(focusedRelationIds ?? [])),
    [focusedRelationIds],
  )
  const selectedRelations = useMemo(
    () => connectedRelationIds(normalized, selectedNodeId),
    [normalized, selectedNodeId],
  )
  const activeRelations = selectedNodeId ? selectedRelations : focused
  const hasFocus = Boolean(selectedNodeId) || activeRelations.size > 0
  const layout = useMemo(() => layoutOntologyNodes(normalized), [normalized])
  const presentationRelationships = useMemo(
    () => orientOntologyRelationships(normalized, layout),
    [layout, normalized],
  )
  const focusedNodeIds = useMemo(() => {
    if (!hasFocus) return new Set()
    return new Set([
      ...(selectedNodeId ? [selectedNodeId] : []),
      (normalized?.relationships ?? [])
        .filter((relationship) => activeRelations.has(relationship.id))
        .flatMap((relationship) => [relationship.source, relationship.target]),
    ].flat())
  }, [activeRelations, hasFocus, normalized, selectedNodeId])
  const nodes = useMemo(
    () =>
      layout.map(({ node, position, root }) => ({
        id: node.id,
        position,
        data: { node, label: nodeLabel(node) },
        selected: node.id === selectedNodeId,
        // 다른 노드를 선택하면 조회 root Chamber의 시각 포커스를 해제한다.
        // root_node_id 자체는 조회 문맥으로 유지하되 그래프 강조는 사용자의 현재 선택을 따른다.
        style: nodeStyle(
          node,
          root && !selectedNodeId,
          node.id === selectedNodeId,
          hasFocus && !focusedNodeIds.has(node.id),
          viewport === 'page' ? 0.62 : 0.34,
        ),
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        draggable: false,
        connectable: false,
        selectable: Boolean(onSelectNode),
      })),
    [focusedNodeIds, hasFocus, layout, onSelectNode, selectedNodeId, viewport],
  )
  const initialEvidenceNodes = useMemo(
    () => (viewport === 'compact' && focused.size > 0 ? nodes.filter((node) => focusedNodeIds.has(node.id)) : undefined),
    [focused, focusedNodeIds, nodes, viewport],
  )
  const focusViewportNodeIds = useMemo(() => [...focusedNodeIds].sort(), [focusedNodeIds])
  const edges = useMemo(() => {
    const firstRelationByType = new Map()
    for (const relationship of presentationRelationships) {
      if (!firstRelationByType.has(relationship.type)) firstRelationByType.set(relationship.type, relationship.id)
    }
    return presentationRelationships.map((relationship) => {
        const isFocused = activeRelations.has(relationship.id)
        const isDimmed = hasFocus && !isFocused
        const showLabel = isFocused || firstRelationByType.get(relationship.type) === relationship.id
        return {
          id: relationship.id,
          source: relationship.display_source,
          target: relationship.display_target,
          label: showLabel
            ? (relationship.display_reversed
                ? ONTOLOGY_REVERSED_RELATION_LABELS[relationship.type]
                : ONTOLOGY_RELATION_LABELS[relationship.type]) ?? relationship.type
            : undefined,
          type: 'smoothstep',
          animated: false,
          selectable: false,
          focusable: false,
          markerEnd: { type: MarkerType.ArrowClosed, color: isFocused ? '#2563eb' : '#94a3b8' },
          style: {
            stroke: isFocused ? '#2563eb' : '#94a3b8',
            strokeWidth: isFocused ? 3 : 1.5,
            opacity: isDimmed ? 0.2 : 1,
          },
          labelStyle: {
            fill: isFocused ? '#1d4ed8' : '#64748b',
            fontSize: 10,
            fontWeight: isFocused ? 700 : 600,
            opacity: isDimmed ? 0.25 : 1,
          },
          labelBgStyle: { fill: '#ffffff', fillOpacity: isDimmed ? 0.2 : 0.92 },
        }
      })
  }, [activeRelations, hasFocus, presentationRelationships])

  return (
    <div className="flex w-full flex-col gap-2.5" data-testid="ontology-graph-canvas">
      <div
        className={`${viewport === 'page' ? 'h-[760px] min-h-[620px]' : 'h-[540px] min-h-[440px]'} w-full overflow-hidden rounded-[10px] border border-line bg-white`}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          fitView
          fitViewOptions={{
            nodes: initialEvidenceNodes,
            padding: initialEvidenceNodes ? 0.75 : 0.14,
            minZoom: 0.38,
            maxZoom: initialEvidenceNodes ? 1.05 : 1.2,
          }}
          minZoom={0.35}
          maxZoom={1.5}
          zoomOnScroll
          zoomOnPinch
          panOnDrag
          nodesDraggable={false}
          nodesConnectable={false}
          edgesReconnectable={false}
          elementsSelectable={Boolean(onSelectNode)}
          connectOnClick={false}
          deleteKeyCode={null}
          onNodeClick={onSelectNode ? (_event, flowNode) => onSelectNode(flowNode.data.node) : undefined}
          onPaneClick={onSelectNode ? () => onSelectNode(null) : undefined}
          aria-label="설비 온톨로지 관계 그래프"
        >
          <ViewportFocus active={hasFocus} nodeIds={focusViewportNodeIds} />
          <Background color="#e2e8f0" gap={24} size={1} />
          <Controls position="top-right" showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  )
}

export default OntologyGraphCanvas
