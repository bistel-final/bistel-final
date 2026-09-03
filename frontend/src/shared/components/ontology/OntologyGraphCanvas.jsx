import { useEffect, useMemo } from 'react'
import { Background, BaseEdge, Controls, EdgeLabelRenderer, Handle, MarkerType, Position, ReactFlow, useReactFlow } from '@xyflow/react'
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

const nodeStyle = (
  node,
  isRoot,
  isSelected,
  isDimmed,
  dimOpacity,
  isDirectImpact,
  isCheckRequired,
  isScope,
  isIncident,
  isWaferSelection,
) => {
  const meta = ONTOLOGY_NODE_META[node.label]
  const isEmphasized = isRoot || isSelected || isDirectImpact || isCheckRequired || isWaferSelection
  const impactColor = isCheckRequired ? '#a16207' : '#2563eb'
  const contextColor = isIncident ? '#ea580c' : isScope ? '#2563eb' : isWaferSelection ? meta.color : null
  const contextBackground = isIncident ? '#fff7ed' : isScope ? '#eff6ff' : isWaferSelection ? `${meta.color}14` : null
  return {
    width: node.label === 'Lot' ? 180 : node.label === 'Wafer' ? 132 : 150,
    minHeight: 58,
    border: `${isSelected || isDirectImpact || isCheckRequired ? 3 : contextColor || isRoot ? 2.5 : 1.5}px ${isCheckRequired ? 'dashed' : 'solid'} ${isDirectImpact || isCheckRequired ? impactColor : contextColor ?? meta.color}`,
    borderRadius: 12,
    background: isDirectImpact ? '#eff6ff' : isCheckRequired ? '#fffbeb' : isSelected ? `${meta.color}22` : contextBackground ?? (isEmphasized ? `${meta.color}22` : '#ffffff'),
    boxShadow: isSelected
      ? `0 0 0 6px ${meta.color}22, 0 8px 20px rgba(15, 23, 42, 0.12)`
      : isDirectImpact || isCheckRequired
        ? `0 0 0 5px ${impactColor}1c, 0 7px 18px rgba(15, 23, 42, 0.10)`
        : contextColor
          ? `0 0 0 3px ${contextColor}18`
        : isRoot
          ? `0 0 0 4px ${meta.color}16`
        : '0 4px 12px rgba(15, 23, 42, 0.06)',
    color: '#0f172a',
    padding: '9px 12px',
    opacity: isDimmed ? dimOpacity : 1,
    transition: 'opacity 160ms ease, box-shadow 160ms ease',
  }
}

const nodeLabel = (node, { isScope, isIncident, isSelected, isWaferSelection }) => {
  const meta = ONTOLOGY_NODE_META[node.label]
  const alarmCount = Number(node.properties?.alarm_count ?? 0)
  const waferCount = node.label === 'Lot' ? Number(node.properties?.wafer_count ?? 0) : 0
  const routeOrder = node.label === 'Chamber' ? Number(node.properties?.lot_route_order ?? 0) : 0
  return (
    <div className="min-w-0 text-center">
      <div className="text-[9px] font-extrabold tracking-[.08em]" style={{ color: meta.color }}>
        {meta.shortLabel}
      </div>
      <div className="mt-1 truncate font-mono text-[11px] font-extrabold" title={node.display_name}>
        {node.display_name}
      </div>
      {(isScope || isIncident || isSelected || isWaferSelection) && (
        <div className="mt-1 flex flex-wrap justify-center gap-1 text-[7px] font-extrabold tracking-[.08em]">
          {isScope && <span className="rounded-full px-1.5 py-0.5 text-white" style={{ backgroundColor: '#2563eb' }}>조회 기준</span>}
          {isIncident && <span className="rounded-full px-1.5 py-0.5 text-white" style={{ backgroundColor: '#ea580c' }}>선택 LOT</span>}
          {isSelected && <span className="rounded-full px-1.5 py-0.5 text-white" style={{ backgroundColor: '#0f172a' }}>상세 확인</span>}
          {isWaferSelection && <span className="rounded-full px-1.5 py-0.5 text-white" style={{ backgroundColor: meta.color }}>선택 WAFER</span>}
        </div>
      )}
      {waferCount > 0 && <div className="mt-1 text-[8px] font-bold text-faint">WAFER {waferCount}</div>}
      {routeOrder > 0 && <div className="mt-1 text-[8px] font-bold text-faint">ROUTE {routeOrder}</div>}
      {alarmCount > 0 && (
        <div className="mt-1 inline-flex rounded-full bg-red-50 px-1.5 py-0.5 text-[8px] font-extrabold text-red-600">
          ALARM {alarmCount}
        </div>
      )}
    </div>
  )
}

// 기본 관계는 좌→우로 둔다. LOT 처리 이력은 별도 edge가 Chamber의 왼쪽 handle에서
// 시작해 설비 구조선과 겹치지 않는 하단 버스로 연결한다.
function OntologyFlowNode({ data }) {
  return (
    <>
      <Handle type="target" position={Position.Left} id="left" isConnectable={false} style={{ opacity: 0 }} />
      <Handle type="target" position={Position.Top} id="top" isConnectable={false} style={{ opacity: 0 }} />
      <Handle type="target" position={Position.Bottom} id="bottom-target" isConnectable={false} style={{ opacity: 0 }} />
      {data.label}
      <Handle type="source" position={Position.Left} id="left-source" isConnectable={false} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right} id="right" isConnectable={false} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Top} id="top-source" isConnectable={false} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom} id="bottom" isConnectable={false} style={{ opacity: 0 }} />
    </>
  )
}

// Chamber → LOT은 기존 설비 관계를 통과하지 않는 별도 처리 이력 버스다.
// 왼쪽 여백으로 빠져 아래까지 내려간 후 LOT 직전에서 분기한다.
function LotHistoryEdge({ sourceX, sourceY, targetX, targetY, label, markerEnd, style }) {
  const busX = sourceX - 150
  const branchY = targetY - 38
  const edgePath = `M ${sourceX} ${sourceY} L ${busX} ${sourceY} L ${busX} ${branchY} L ${targetX} ${branchY} L ${targetX} ${targetY}`
  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={style} />
      {label && (
        <EdgeLabelRenderer>
          <div
            className="pointer-events-none absolute rounded bg-white px-1.5 py-0.5 text-[10px] font-semibold text-g2"
            style={{ transform: `translate(-50%, -50%) translate(${busX + 26}px, ${(sourceY + branchY) / 2}px)` }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

// LOT 단독 조회의 실제 Chamber 방문은 LOT 아래의 공통 세로 trunk에서 분기한다.
function LotRouteEdge({ sourceX, sourceY, targetX, targetY, label, markerEnd, style }) {
  const edgePath = `M ${sourceX} ${sourceY} L ${sourceX} ${targetY} L ${targetX} ${targetY}`
  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={style} />
      {label && (
        <EdgeLabelRenderer>
          <div
            className="pointer-events-none absolute rounded bg-white px-1.5 py-0.5 text-[10px] font-semibold text-g2"
            style={{ transform: `translate(-50%, -50%) translate(${sourceX + 28}px, ${(sourceY + targetY) / 2}px)` }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

function NextStepEdge({ sourceX, sourceY, targetX, targetY, label, markerEnd, style }) {
  const edgePath = `M ${sourceX} ${sourceY} L ${targetX} ${targetY}`
  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={style} />
      {label && (
        <EdgeLabelRenderer>
          <div
            className="pointer-events-none absolute rounded bg-white px-1.5 py-0.5 text-[10px] font-semibold text-g2"
            style={{ transform: `translate(-50%, -50%) translate(${(sourceX + targetX) / 2}px, ${(sourceY + targetY) / 2}px)` }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

function LotContainsEdge({ sourceX, sourceY, targetX, targetY, label, markerEnd, style }) {
  const isHorizontal = Math.abs(sourceY - targetY) < 8
  const branchY = targetY - 48
  // 모든 Wafer edge가 LOT에서 출발하므로, 관계명은 각 개별 분기 중간이 아니라
  // LOT 바로 아래의 공통 trunk에 한 번만 둔다.
  const labelY = isHorizontal ? sourceY - 16 : sourceY + 24
  const labelX = isHorizontal ? (sourceX + targetX) / 2 : sourceX
  const edgePath = isHorizontal
    ? `M ${sourceX} ${sourceY} L ${targetX} ${targetY}`
    : `M ${sourceX} ${sourceY} L ${sourceX} ${branchY} L ${targetX} ${branchY} L ${targetX} ${targetY}`
  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={style} />
      {label && (
        <EdgeLabelRenderer>
          <div
            className="pointer-events-none absolute rounded bg-white px-1.5 py-0.5 text-[10px] font-semibold text-g2"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

function WaferHistoryEdge({ sourceX, sourceY, targetX, targetY, label, markerEnd, style }) {
  const edgePath = `M ${sourceX} ${sourceY} L ${sourceX} ${targetY} L ${targetX} ${targetY}`
  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={style} />
      {label && (
        <EdgeLabelRenderer>
          <div
            className="pointer-events-none absolute rounded bg-white px-1.5 py-0.5 text-[10px] font-semibold text-g2"
            style={{ transform: `translate(-50%, -50%) translate(${sourceX + 30}px, ${sourceY + 24}px)` }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

const nodeTypes = { ontology: OntologyFlowNode }
const edgeTypes = { contains: LotContainsEdge, waferHistory: WaferHistoryEdge, lotHistory: LotHistoryEdge, lotRoute: LotRouteEdge, lotIncident: NextStepEdge, nextStep: NextStepEdge }

function ViewportFocus({ active, nodeIds, graphKey, padding = 0.65, maxZoom = 1.05 }) {
  const { fitView } = useReactFlow()
  const nodeKey = nodeIds.join('|')
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      fitView({
        nodes: active ? nodeIds.map((id) => ({ id })) : undefined,
        padding: active ? padding : 0.14,
        minZoom: 0.38,
        maxZoom: active ? maxZoom : 1.2,
        duration: 320,
      })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [active, fitView, graphKey, maxZoom, nodeIds, nodeKey, padding])
  return null
}

function OntologyGraphCanvas({
  graph,
  focusedRelationIds = new Set(),
  impactNodeIds = new Set(),
  checkRequiredNodeIds = new Set(),
  selectedNodeId = null,
  scopeNodeId = null,
  incidentNodeId = null,
  waferSelectionNodeId = null,
  onSelectNode = null,
  viewport = 'compact',
  emphasizeRoot = true,
  chamberOnly = false,
}) {
  const modalViewport = viewport === 'modal'
  const normalized = useMemo(() => normalizeOntologyGraph(graph), [graph])
  const focused = useMemo(
    () => (focusedRelationIds instanceof Set ? focusedRelationIds : new Set(focusedRelationIds ?? [])),
    [focusedRelationIds],
  )
  const directImpact = useMemo(
    () => (impactNodeIds instanceof Set ? impactNodeIds : new Set(impactNodeIds ?? [])),
    [impactNodeIds],
  )
  const checkRequired = useMemo(
    () => (checkRequiredNodeIds instanceof Set ? checkRequiredNodeIds : new Set(checkRequiredNodeIds ?? [])),
    [checkRequiredNodeIds],
  )
  const selectedRelations = useMemo(
    () => connectedRelationIds(normalized, selectedNodeId),
    [normalized, selectedNodeId],
  )
  const layout = useMemo(() => layoutOntologyNodes(normalized), [normalized])
  const presentationRelationships = useMemo(
    () => orientOntologyRelationships(normalized, layout, { chamberOnly }),
    [chamberOnly, layout, normalized],
  )
  const impactRelations = useMemo(() => {
    const explicitNodes = new Set([...directImpact, ...checkRequired])
    return new Set((normalized?.relationships ?? [])
      .filter((relationship) => explicitNodes.has(relationship.source) || explicitNodes.has(relationship.target))
      .map((relationship) => relationship.id))
  }, [checkRequired, directImpact, normalized])
  const activeRelations = useMemo(
    () => selectedNodeId ? selectedRelations : new Set([...focused, ...impactRelations]),
    [focused, impactRelations, selectedNodeId, selectedRelations],
  )
  const hasFocus = Boolean(selectedNodeId) || activeRelations.size > 0 || directImpact.size > 0 || checkRequired.size > 0
  const focusedNodeIds = useMemo(() => {
    if (!hasFocus) return new Set()
    return new Set([
      ...(selectedNodeId ? [selectedNodeId] : []),
      ...directImpact,
      ...checkRequired,
      (normalized?.relationships ?? [])
        .filter((relationship) => activeRelations.has(relationship.id))
        .flatMap((relationship) => [relationship.source, relationship.target]),
    ].flat())
  }, [activeRelations, checkRequired, directImpact, hasFocus, normalized, selectedNodeId])
  const nodes = useMemo(
    () => {
      const ontologyNodes = layout.map(({ node, position, root }) => ({
        id: node.id,
        type: 'ontology',
        position,
        data: {
          node,
          label: nodeLabel(node, {
            isScope: node.id === scopeNodeId,
            isIncident: node.id === incidentNodeId,
            isSelected: node.id === selectedNodeId,
            isWaferSelection: node.id === waferSelectionNodeId,
          }),
        },
        selected: node.id === selectedNodeId,
        // 다른 노드를 선택하면 조회 root Chamber의 시각 포커스를 해제한다.
        // root_node_id 자체는 조회 문맥으로 유지하되 그래프 강조는 사용자의 현재 선택을 따른다.
        style: nodeStyle(
          node,
          root && !selectedNodeId && emphasizeRoot,
          node.id === selectedNodeId,
          hasFocus && !focusedNodeIds.has(node.id),
          viewport === 'page' ? 0.62 : 0.34,
          directImpact.has(node.id),
          checkRequired.has(node.id),
          node.id === scopeNodeId,
          node.id === incidentNodeId,
          node.id === waferSelectionNodeId,
        ),
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        draggable: false,
        connectable: false,
        selectable: Boolean(onSelectNode),
      }))
      return ontologyNodes
    },
    [checkRequired, directImpact, emphasizeRoot, focusedNodeIds, hasFocus, incidentNodeId, layout, onSelectNode, scopeNodeId, selectedNodeId, viewport, waferSelectionNodeId],
  )
  const initialEvidenceNodes = useMemo(
    () => (viewport === 'compact' && focused.size > 0 ? nodes.filter((node) => focusedNodeIds.has(node.id)) : undefined),
    [focused, focusedNodeIds, nodes, viewport],
  )
  const focusViewportNodeIds = useMemo(
    () => (modalViewport && directImpact.size + checkRequired.size > 0
      ? [...new Set([
          ...directImpact,
          ...checkRequired,
          ...(normalized?.nodes ?? [])
            .filter((node) => node.label === 'EquipmentModel')
            .map((node) => node.id),
        ])].sort()
      : [...focusedNodeIds].sort()),
    [checkRequired, directImpact, focusedNodeIds, modalViewport, normalized],
  )
  const layoutKey = useMemo(
    () => layout.map(({ node, position }) => `${node.id}:${position.x}:${position.y}`).join('|'),
    [layout],
  )
  const edges = useMemo(() => {
    const nodesById = new Map((normalized?.nodes ?? []).map((node) => [node.id, node]))
    const hasWaferContext = (normalized?.nodes ?? []).some((node) => node.label === 'Wafer')
    const isWaferSelection = (normalized?.nodes ?? []).filter((node) => node.label === 'Wafer').length === 1 &&
      (normalized?.nodes ?? []).some((node) => node.properties?.lot_route_order != null)
    const firstRelationByType = new Map()
    for (const relationship of presentationRelationships) {
      if (!firstRelationByType.has(relationship.type)) firstRelationByType.set(relationship.type, relationship.id)
    }
    const primaryLotContainsId = presentationRelationships.find((relationship) =>
      relationship.type === 'CONTAINS' &&
      nodesById.get(relationship.display_source)?.label === 'Lot' &&
      nodesById.get(relationship.display_target)?.label === 'Wafer',
    )?.id
    return presentationRelationships.map((relationship) => {
        const isLotHistory = relationship.type === 'PROCESSED_IN' &&
          nodesById.get(relationship.display_source)?.label === 'Chamber' &&
          nodesById.get(relationship.display_target)?.label === 'Lot' &&
          nodesById.get(relationship.display_source)?.properties?.lot_route_order == null &&
          !hasWaferContext
        const isLotIncident = relationship.type === 'PROCESSED_IN' &&
          nodesById.get(relationship.display_source)?.label === 'Chamber' &&
          nodesById.get(relationship.display_target)?.label === 'Lot' &&
          hasWaferContext
        const isLotContains = relationship.type === 'CONTAINS' &&
          nodesById.get(relationship.display_source)?.label === 'Lot' &&
          nodesById.get(relationship.display_target)?.label === 'Wafer'
        const isLotRoute = relationship.type === 'PROCESSED_IN' &&
          nodesById.get(relationship.display_source)?.label === 'Lot' &&
          nodesById.get(relationship.display_target)?.label === 'Chamber' &&
          nodesById.get(relationship.display_target)?.properties?.lot_route_order != null
        const isNextStep = (relationship.type === 'NEXT_STEP' && relationship.display_vertical) || relationship.display_straight
        const isWaferHistory = isWaferSelection && relationship.type === 'PROCESSED_IN' && nodesById.get(relationship.display_source)?.label === 'Wafer' && nodesById.get(relationship.display_target)?.label === 'Chamber'
        const isFocused = activeRelations.has(relationship.id)
        const isDimmed = hasFocus && !isFocused
        const showLabel = isFocused || firstRelationByType.get(relationship.type) === relationship.id
        const relationshipColor = isFocused ? '#2563eb' : '#94a3b8'
        return {
          id: relationship.id,
          source: relationship.display_source,
          target: relationship.display_target,
          label: isLotContains && relationship.id === primaryLotContainsId
            ? '포함 웨이퍼'
            : showLabel
              ? (relationship.display_reversed
                  ? ONTOLOGY_REVERSED_RELATION_LABELS[relationship.type]
                  : ONTOLOGY_RELATION_LABELS[relationship.type]) ?? relationship.type
              : undefined,
          type: isLotHistory ? 'lotHistory' : isLotRoute ? 'lotRoute' : isLotIncident ? 'lotIncident' : isLotContains ? 'contains' : isWaferHistory ? 'waferHistory' : isNextStep ? 'nextStep' : 'smoothstep',
          animated: false,
          selectable: false,
          focusable: false,
          markerEnd: { type: MarkerType.ArrowClosed, color: relationshipColor },
          sourceHandle: isLotHistory ? 'left-source' : isWaferHistory ? 'bottom' : isLotRoute || isLotIncident || isLotContains ? 'bottom' : relationship.display_vertical_up ? 'top-source' : relationship.display_vertical ? 'bottom' : 'right',
          targetHandle: isLotRoute ? 'left' : isWaferHistory ? 'left' : isLotIncident || isLotContains ? 'top' : relationship.display_vertical_up ? 'bottom-target' : relationship.display_vertical ? 'top' : 'left',
          style: {
            stroke: relationshipColor,
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
  }, [activeRelations, hasFocus, normalized, presentationRelationships])

  return (
    <div className={`flex w-full flex-col gap-2.5 ${modalViewport ? 'h-full min-h-0' : ''}`} data-testid="ontology-graph-canvas">
      <div
        className={`${viewport === 'page' ? 'h-[760px] min-h-[620px]' : modalViewport ? 'h-full min-h-[500px]' : 'h-[400px] min-h-[340px]'} w-full overflow-hidden bg-white ${modalViewport ? '' : 'rounded-[10px] border border-line'}`}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
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
          <ViewportFocus
            active={hasFocus}
            nodeIds={focusViewportNodeIds}
            graphKey={layoutKey}
            padding={modalViewport ? 0.08 : 0.65}
            maxZoom={modalViewport ? 1.25 : 1.05}
          />
          <Background color="#e2e8f0" gap={24} size={1} />
          <Controls position="top-right" showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  )
}

export default OntologyGraphCanvas
