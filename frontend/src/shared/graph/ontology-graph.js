export const ONTOLOGY_NODE_LABELS = Object.freeze([
  'Chamber',
  'Equipment',
  'EquipmentModel',
  'Area',
  'ProcessStep',
  'Parameter',
  'Lot',
  'Wafer',
])

export const ONTOLOGY_NODE_META = Object.freeze({
  Area: { shortLabel: 'AREA', color: '#0f766e' },
  Chamber: { shortLabel: 'CHAMBER', color: '#2563eb' },
  Equipment: { shortLabel: 'EQP', color: '#16a34a' },
  EquipmentModel: { shortLabel: 'MODEL', color: '#7c3aed' },
  Parameter: { shortLabel: 'PARAM', color: '#0ea5e9' },
  ProcessStep: { shortLabel: 'STEP', color: '#f59e0b' },
  Lot: { shortLabel: 'LOT', color: '#ea580c' },
  Wafer: { shortLabel: 'WAFER', color: '#db2777' },
})

export const ONTOLOGY_RELATION_LABELS = Object.freeze({
  IN_AREA: '공정 영역',
  MEASURED_ON: '측정 챔버',
  NEXT_STEP: '다음 공정',
  OF_MODEL: '설비 모델',
  PART_OF: '소속 설비',
  PERFORMS: '수행 공정',
  CONTAINS: '포함 웨이퍼',
  PROCESSED_IN: '처리 챔버',
  ALARM_ON: '알람 발생 파라미터',
})

export const ONTOLOGY_REVERSED_RELATION_LABELS = Object.freeze({
  IN_AREA: '포함 공정',
  MEASURED_ON: '측정 파라미터',
  OF_MODEL: '적용 설비',
  PART_OF: '구성 챔버',
  PERFORMS: '공정 수행 설비',
  CONTAINS: '소속 LOT',
  PROCESSED_IN: '처리 이력',
  ALARM_ON: '알람 발생 Wafer',
})

// Neo4j node properties 전체를 화면에 노출하지 않는다. 식별자·표시명에 필요한 공개 키만 허용한다.
export const PUBLIC_NODE_PROPERTIES = Object.freeze({
  Area: Object.freeze(['area_id', 'area_name']),
  Chamber: Object.freeze(['chamber_id']),
  Equipment: Object.freeze(['equipment_id', 'equipment_name']),
  EquipmentModel: Object.freeze(['model_code', 'model_name']),
  Parameter: Object.freeze(['parameter_id']),
  ProcessStep: Object.freeze(['step_id', 'step_name', 'step_seq']),
  Lot: Object.freeze(['lot_id']),
  Wafer: Object.freeze([
    'lot_hist_id', 'lot_id', 'wafer_id', 'wafer_no', 'step_id', 'recipe_id',
    'track_in_at', 'track_out_at', 'chamber_wafer_cum', 'wafer_count', 'alarm_count', 'source_system',
  ]),
})

const supportedLabels = new Set(ONTOLOGY_NODE_LABELS)

const normalizeNode = (node) => {
  const id = node?.id ?? node?.node_id
  const label = node?.label
  const businessId = node?.business_id
  if (!id || !supportedLabels.has(label) || !businessId) return null
  return {
    id: String(id),
    label,
    business_id: String(businessId),
    display_name: String(node.display_name ?? node.name ?? businessId),
    properties: node.properties && typeof node.properties === 'object' ? node.properties : {},
  }
}

const normalizeRelationship = (relationship) => {
  const id = relationship?.id ?? relationship?.relation_id
  const source = relationship?.source ?? relationship?.from_node_id
  const target = relationship?.target ?? relationship?.to_node_id
  const type = relationship?.type
  if (!id || !source || !target || !type) return null
  return { id: String(id), type: String(type), source: String(source), target: String(target) }
}

export function normalizeOntologyGraph(graph) {
  if (!graph || typeof graph !== 'object') return null
  const nodes = (graph.nodes ?? []).map(normalizeNode).filter(Boolean)
  const nodeIds = new Set(nodes.map((node) => node.id))
  const relationships = (graph.relationships ?? [])
    .map(normalizeRelationship)
    .filter((relationship) => relationship && nodeIds.has(relationship.source) && nodeIds.has(relationship.target))
  const requestedRoot = graph.root_node_id ?? (graph.context?.chamber_id ? `Chamber:${graph.context.chamber_id}` : null)
  const rootNodeId = nodeIds.has(requestedRoot) ? requestedRoot : (nodes.find((node) => node.label === 'Chamber')?.id ?? nodes[0]?.id)
  return {
    root_node_id: rootNodeId ?? null,
    nodes,
    relationships,
    graph_revision: graph.graph_revision ? String(graph.graph_revision) : null,
  }
}

export const hasDisplayableRelationships = (graph) => Boolean(graph?.relationships?.length)

export function connectedRelationIds(graph, nodeId) {
  if (!nodeId) return new Set()
  const normalized = normalizeOntologyGraph(graph)
  return new Set(
    (normalized?.relationships ?? [])
      .filter((relationship) => relationship.source === nodeId || relationship.target === nodeId)
      .map((relationship) => relationship.id),
  )
}

export function publicNodeDetails(node) {
  if (!node) return []
  const allowed = PUBLIC_NODE_PROPERTIES[node.label] ?? []
  return allowed
    .filter((key) => node.properties?.[key] != null && node.properties[key] !== '')
    .map((key) => ({ key, value: String(node.properties[key]) }))
}

export function summarizeOntologyAlarms(alarms) {
  const rows = Array.isArray(alarms) ? alarms : []
  const alarmType = (alarm) => alarm?.alarm_type ?? alarm?.judgement
  const actionIds = new Set(rows.map((alarm) => alarm?.action_id).filter(Boolean))
  const pendingActionIds = new Set(
    rows
      .filter((alarm) => alarm?.approval_status === 'PENDING')
      .map((alarm) => alarm?.action_id)
      .filter(Boolean),
  )
  const occurredAt = rows
    .map((alarm) => alarm?.occurred_at)
    .filter(Boolean)
    .sort((a, b) => String(b).localeCompare(String(a)))[0] ?? null
  return {
    total: rows.length,
    oos: rows.filter((alarm) => alarmType(alarm) === 'OOS').length,
    ooc: rows.filter((alarm) => alarmType(alarm) === 'OOC').length,
    actions: actionIds.size,
    pending_actions: pendingActionIds.size,
    latest_occurred_at: occurredAt ? String(occurredAt) : null,
  }
}

const nodeById = (graph, nodeId) => graph.nodes.find((node) => node.id === nodeId)

export function ontologyAlarmScope(graph, node) {
  const normalized = normalizeOntologyGraph(graph)
  if (!normalized || !node) return null

  if (node.label === 'Parameter') {
    return { requests: [{ sensor_id: node.business_id }], basis: `파라미터 ${node.business_id}` }
  }
  if (node.label === 'Wafer') {
    const focusChamberId = node.properties?.alarm_focus_chamber_id
    const chamber = normalized.relationships
      .filter((relationship) => relationship.type === 'PROCESSED_IN' && relationship.source === node.id)
      .map((relationship) => nodeById(normalized, relationship.target))
      .find((related) => related?.id === focusChamberId)
      ?? normalized.relationships
        .filter((relationship) => relationship.type === 'PROCESSED_IN' && relationship.source === node.id)
        .map((relationship) => nodeById(normalized, relationship.target))
        .find((related) => related?.label === 'Chamber')
      ?? nodeById(normalized, normalized.root_node_id)
    const lotHistId = node.properties?.lot_hist_id
    const lotId = node.properties?.lot_id
    if (chamber && lotHistId && lotId) {
      return {
        requests: [{ chamber_id: chamber.business_id }],
        lot_id: String(lotId),
        lot_hist_id: String(lotHistId),
        chamber_id: chamber.business_id,
        wafer: true,
        basis: `Wafer ${node.business_id} · ${chamber.business_id}`,
      }
    }
  }
  if (node.label === 'Lot') {
    const chamber = nodeById(normalized, normalized.root_node_id)
    if (chamber?.label === 'Chamber') {
      return {
        requests: [{ chamber_id: chamber.business_id }],
        lot_id: node.business_id,
        chamber_id: chamber.business_id,
        incident: true,
        basis: `Incident ${node.business_id} · ${chamber.business_id}`,
      }
    }
  }
  if (node.label === 'Chamber') {
    return { requests: [{ chamber_id: node.business_id }], basis: `챔버 ${node.business_id}` }
  }
  if (node.label === 'Equipment') {
    return { requests: [{ equipment_id: node.business_id }], basis: `설비 ${node.business_id}` }
  }
  if (node.label === 'Area') {
    return { requests: [{ area: node.business_id }], basis: `구역 ${node.business_id}` }
  }

  if (node.label === 'EquipmentModel') {
    const equipmentIds = normalized.relationships
      .filter((relationship) => relationship.type === 'OF_MODEL' && relationship.target === node.id)
      .map((relationship) => nodeById(normalized, relationship.source))
      .filter((related) => related?.label === 'Equipment')
      .map((related) => related.business_id)
    if (equipmentIds.length > 0) {
      return {
        requests: [...new Set(equipmentIds)].sort().map((equipmentId) => ({ equipment_id: equipmentId })),
        basis: `모델 연결 설비 ${new Set(equipmentIds).size}대`,
      }
    }
  }

  if (node.label === 'ProcessStep') {
    const area = normalized.relationships
      .filter((relationship) => relationship.type === 'IN_AREA' && relationship.source === node.id)
      .map((relationship) => nodeById(normalized, relationship.target))
      .find((related) => related?.label === 'Area')
    if (area) return { requests: [{ area: area.business_id }], basis: `공정 구역 ${area.business_id}` }

    const equipment = normalized.relationships
      .filter((relationship) => relationship.type === 'PERFORMS' && relationship.target === node.id)
      .map((relationship) => nodeById(normalized, relationship.source))
      .find((related) => related?.label === 'Equipment')
    if (equipment) {
      return {
        requests: [{ equipment_id: equipment.business_id }],
        basis: `공정 수행 설비 ${equipment.business_id}`,
      }
    }
  }

  return null
}

export function mergeOntologyGraphs(graphs, rootNodeId = null) {
  const normalizedGraphs = (Array.isArray(graphs) ? graphs : []).map(normalizeOntologyGraph).filter(Boolean)
  if (normalizedGraphs.length === 0) return null
  const revisions = new Set(normalizedGraphs.map((graph) => graph.graph_revision).filter(Boolean))
  if (revisions.size > 1) return null
  const nodes = new Map()
  const relationships = new Map()
  const relationshipTuples = new Set()
  for (const graph of normalizedGraphs) {
    for (const node of graph.nodes) nodes.set(node.id, node)
    for (const relationship of graph.relationships) {
      const tuple = `${relationship.type}\u0000${relationship.source}\u0000${relationship.target}`
      if (relationshipTuples.has(tuple)) continue
      let displayId = relationship.id
      const existing = relationships.get(displayId)
      if (
        existing &&
        (existing.type !== relationship.type || existing.source !== relationship.source || existing.target !== relationship.target)
      ) {
        displayId = `${relationship.id}::${relationship.source}->${relationship.target}`
      }
      relationships.set(displayId, { ...relationship, id: displayId, canonical_id: relationship.id })
      relationshipTuples.add(tuple)
    }
  }
  const requestedRoot = rootNodeId && nodes.has(rootNodeId) ? rootNodeId : normalizedGraphs[0].root_node_id
  return {
    root_node_id: requestedRoot,
    nodes: [...nodes.values()],
    relationships: [...relationships.values()],
    graph_revision: [...revisions][0] ?? null,
  }
}

const productionLots = (graph) => {
  const normalized = normalizeOntologyGraph(graph)
  if (!normalized) return []
  const nodesById = new Map(normalized.nodes.map((node) => [node.id, node]))
  const wafersByLot = new Map()
  for (const relationship of normalized.relationships) {
    if (relationship.type !== 'CONTAINS') continue
    const lot = nodesById.get(relationship.source)
    const wafer = nodesById.get(relationship.target)
    if (lot?.label !== 'Lot' || wafer?.label !== 'Wafer') continue
    const chamberIds = normalized.relationships
      .filter((item) => item.type === 'PROCESSED_IN' && item.source === wafer.id)
      .map((item) => item.target)
    const lotWafers = wafersByLot.get(lot.id) ?? new Map()
    const waferKey = String(wafer.properties?.wafer_id ?? wafer.business_id)
    const existing = lotWafers.get(waferKey) ?? { node: wafer, chamberIds: new Set(), historyNodeByChamber: new Map() }
    chamberIds.forEach((chamberId) => {
      existing.chamberIds.add(chamberId)
      existing.historyNodeByChamber.set(chamberId, wafer)
    })
    lotWafers.set(waferKey, existing)
    wafersByLot.set(lot.id, lotWafers)
  }
  return sorted(normalized.nodes.filter((node) => node.label === 'Lot')).map((lot) => ({
    lot,
    wafers: [...(wafersByLot.get(lot.id)?.values() ?? [])],
  }))
}

export function lotOptionsForChamber(graph, chamberId = '') {
  const options = productionLots(graph)
    .filter(({ wafers }) => !chamberId || wafers.some(({ chamberIds }) => chamberIds.has(`Chamber:${chamberId}`)))
    .map(({ lot }) => ({ id: lot.business_id, label: lot.display_name }))
  return options.sort((a, b) => a.id.localeCompare(b.id))
}

// Wafer는 LOT를 선택한 뒤에만 탐색한다. Chamber가 함께 지정된 경우에는 현재
// 그래프에 표시 가능한 해당 Chamber 처리 이력만 남긴다.
export function waferOptionsForLot(graph, lotId = '', chamberId = '') {
  if (!lotId) return []
  const chamberNodeId = chamberId ? `Chamber:${chamberId}` : ''
  const lotContext = productionLots(graph).find(({ lot }) => lot.business_id === lotId)
  if (!lotContext) return []
  return lotContext.wafers
    .filter(({ chamberIds }) => !chamberNodeId || chamberIds.has(chamberNodeId))
    .map(({ node }) => ({ id: node.id, label: node.display_name }))
    .sort((a, b) => a.label.localeCompare(b.label))
}

const staticGraphForChamber = (
  normalized,
  chamberId,
  includeSiblingChambers = false,
  includeAdjacentSteps = true,
) => {
  const staticRelationships = normalized.relationships
    .filter((relationship) => !['CONTAINS', 'PROCESSED_IN'].includes(relationship.type))
  if (!chamberId) return { nodes: normalized.nodes.filter((node) => !['Lot', 'Wafer'].includes(node.label)), relationships: staticRelationships }

  const rootNodeId = `Chamber:${chamberId}`
  const included = new Map()
  const include = (relationship) => included.set(relationship.id, relationship)
  const rootPart = staticRelationships.find((relationship) => relationship.type === 'PART_OF' && relationship.source === rootNodeId)
  if (rootPart) {
    include(rootPart)
    const equipmentId = rootPart.target
    if (includeSiblingChambers) {
      staticRelationships
        .filter((relationship) => relationship.type === 'PART_OF' && relationship.target === equipmentId)
        .forEach(include)
    }
    staticRelationships
      .filter((relationship) => ['OF_MODEL', 'PERFORMS'].includes(relationship.type) && relationship.source === equipmentId)
      .forEach((relationship) => {
        include(relationship)
        if (relationship.type !== 'PERFORMS') return
        const stepId = relationship.target
        staticRelationships
          .filter((item) => item.type === 'IN_AREA' && item.source === stepId)
          .forEach(include)
        if (includeAdjacentSteps) {
          staticRelationships
            .filter((item) => item.type === 'NEXT_STEP' && (item.source === stepId || item.target === stepId))
            .forEach(include)
        }
      })
  }
  staticRelationships
    .filter((relationship) => relationship.type === 'MEASURED_ON' && relationship.target === rootNodeId)
    .forEach(include)
  const nodeIds = new Set([rootNodeId])
  included.forEach((relationship) => {
    nodeIds.add(relationship.source)
    nodeIds.add(relationship.target)
  })
  return { nodes: normalized.nodes.filter((node) => nodeIds.has(node.id)), relationships: [...included.values()] }
}

// LOT 단독 조회는 추정 경로가 아니라 lot_history에서 확인된 Chamber 방문만 사용한다.
const staticGraphForLotRoute = (normalized, lots) => {
  const visits = new Map()
  for (const { wafers } of lots) {
    for (const { chamberIds, historyNodeByChamber } of wafers) {
      for (const chamberNodeId of chamberIds) {
        const historyNode = historyNodeByChamber.get(chamberNodeId)
        const occurredAt = String(historyNode?.properties?.track_in_at ?? '')
        const existing = visits.get(chamberNodeId)
        if (!existing || occurredAt < existing.occurredAt) visits.set(chamberNodeId, { occurredAt })
      }
    }
  }
  const orderedChamberNodeIds = [...visits.entries()]
    .sort(([leftId, left], [rightId, right]) => left.occurredAt.localeCompare(right.occurredAt) || leftId.localeCompare(rightId))
    .map(([chamberNodeId]) => chamberNodeId)
  const nodes = new Map()
  const relationships = new Map()
  // 실제 방문 Chamber마다 필요한 ontology subgraph만 합친다. 형제 Chamber·인접
  // 공정은 넣지 않아 LOT과 무관한 구조가 경로를 가리지 않게 한다.
  orderedChamberNodeIds.forEach((chamberNodeId, index) => {
    const chamber = normalized.nodes.find((node) => node.id === chamberNodeId)
    if (!chamber) return
    const chamberId = chamberNodeId.replace(/^Chamber:/, '')
    const partial = staticGraphForChamber(normalized, chamberId, false, false)
    partial.nodes.forEach((node) => nodes.set(node.id, node))
    partial.relationships.forEach((relationship) => relationships.set(relationship.id, relationship))
    nodes.set(chamberNodeId, {
      ...chamber,
      properties: {
        ...chamber.properties,
        lot_route_order: index + 1,
      },
    })
  })
  const nodeIds = new Set(nodes.keys())
  normalized.relationships
    .filter((relationship) => relationship.type === 'NEXT_STEP' && nodeIds.has(relationship.source) && nodeIds.has(relationship.target))
    .forEach((relationship) => relationships.set(relationship.id, relationship))
  return {
    root_node_id: orderedChamberNodeIds[0] ?? normalized.root_node_id,
    nodes: [...nodes.values()],
    relationships: [...relationships.values()],
  }
}

// 전체 화면은 생산 이력을 숨긴다. Chamber를 고르면 해당 Chamber를 거친 LOT을 요약 node로
// 보여 주고, LOT을 고르면 그 LOT의 실제 wafer를 펼친다. 원본 lot_history row는 그대로
// 유지되며 이 함수는 read model의 화면 projection만 만든다.
export function buildLotContextGraph(graph, selectedLotId = '', selectedChamberId = '', selectedWaferId = '') {
  const normalized = normalizeOntologyGraph(graph)
  if (!normalized) return null
  const chamberNodeId = selectedChamberId ? `Chamber:${selectedChamberId}` : ''
  const selectedLots = productionLots(normalized)
    .filter(({ lot }) => !selectedLotId || lot.business_id === selectedLotId)
    .filter(({ wafers }) => !chamberNodeId || wafers.some(({ chamberIds }) => chamberIds.has(chamberNodeId)))
  // Chamber 단계에서는 같은 설비의 형제 Chamber를 함께 보지만, LOT까지 선택하면
  // incident 범위를 선택 Chamber 하나로 좁히고 인접 공정은 표시하지 않는다.
  const staticGraph = selectedLotId && !selectedChamberId
    ? staticGraphForLotRoute(normalized, selectedLots)
    : staticGraphForChamber(
      normalized,
      selectedChamberId,
      Boolean(selectedChamberId && !selectedLotId),
      !selectedLotId,
    )
  // 첫 진입은 정적 설비 ontology만 보인다.
  if (!selectedLotId && !selectedChamberId) {
    return {
      root_node_id: normalized.root_node_id,
      nodes: staticGraph.nodes,
      relationships: staticGraph.relationships,
      graph_revision: normalized.graph_revision,
    }
  }
  const nodes = new Map(staticGraph.nodes.map((node) => [node.id, node]))
  const relationships = new Map(staticGraph.relationships.map((relationship) => [relationship.id, relationship]))

  for (const { lot, wafers } of selectedLots) {
    const visibleWafers = chamberNodeId
      ? wafers.filter(({ chamberIds }) => chamberIds.has(chamberNodeId))
      : wafers
    nodes.set(lot.id, {
      ...lot,
      properties: { ...lot.properties, wafer_count: visibleWafers.length, source_system: 'POSTGRES_LOT_HISTORY' },
    })
    // Chamber만 선택했을 때는 LOT 단위로만 보여 준다. LOT 선택 뒤에만 wafer 처리 이력을
    // 펼쳐, 하단 LOT row가 과도하게 커지지 않게 한다.
    if (!selectedLotId) {
      relationships.set(`VIEW-LOT-PROCESSED-IN-${lot.id}-${chamberNodeId}`, {
        id: `VIEW-LOT-PROCESSED-IN-${lot.id}-${chamberNodeId}`,
        type: 'PROCESSED_IN', source: lot.id, target: chamberNodeId,
      })
      continue
    }
    // LOT 단독 선택은 wafer별 모든 선을 그리지 않는다. 실제 방문 Chamber와의
    // LOT 단위 처리 이력만 남겨 route를 읽기 쉽게 한다.
    if (!chamberNodeId) {
      const routeChambers = staticGraph.nodes
        .filter((node) => node.label === 'Chamber')
        .sort((left, right) => Number(left.properties?.lot_route_order ?? 999) - Number(right.properties?.lot_route_order ?? 999))
      const firstChamber = routeChambers[0]
      if (firstChamber) {
        routeChambers.forEach((chamber) => {
          relationships.set(`VIEW-LOT-ROUTE-${lot.id}-${chamber.id}`, {
            id: `VIEW-LOT-ROUTE-${lot.id}-${chamber.id}`,
            type: 'PROCESSED_IN', source: lot.id, target: chamber.id,
          })
        })
      }
      // 알람은 lot_hist_id(개별 처리 이력)를 가리킬 수 있다. 동일 물리 Wafer의
      // route는 합쳐 보여 주되, 선택 node에는 알람이 발생한 Chamber를 보존한다.
      const selectedWafer = wafers.flatMap(({ node, chamberIds, historyNodeByChamber }) => [
        { node, chamberIds, focusChamberId: null },
        ...[...historyNodeByChamber.entries()].map(([focusChamberId, historyNode]) => ({
          node: historyNode, chamberIds, focusChamberId,
        })),
      ]).find(({ node }) => node.id === selectedWaferId)
      if (selectedWafer) {
        const waferNode = selectedWafer.focusChamberId
          ? { ...selectedWafer.node, properties: { ...selectedWafer.node.properties, alarm_focus_chamber_id: selectedWafer.focusChamberId } }
          : selectedWafer.node
        nodes.set(waferNode.id, waferNode)
        relationships.set(`VIEW-CONTAINS-${lot.business_id}-${waferNode.id}`, {
          id: `VIEW-CONTAINS-${lot.business_id}-${waferNode.id}`,
          type: 'CONTAINS', source: lot.id, target: selectedWafer.node.id,
        })
        selectedWafer.chamberIds.forEach((historyChamberId) => {
          relationships.set(`VIEW-PROCESSED-IN-${waferNode.id}-${historyChamberId}`, {
            id: `VIEW-PROCESSED-IN-${waferNode.id}-${historyChamberId}`,
            type: 'PROCESSED_IN', source: waferNode.id, target: historyChamberId,
          })
        })
      }
      continue
    }
    const displayedWafers = visibleWafers.map(({ node, chamberIds, historyNodeByChamber }) => ({
      node: chamberNodeId ? historyNodeByChamber.get(chamberNodeId) ?? node : node,
      chamberIds: chamberNodeId ? new Set([chamberNodeId]) : chamberIds,
    }))
    for (const { node, chamberIds } of displayedWafers) {
      nodes.set(node.id, node)
      relationships.set(`VIEW-CONTAINS-${lot.business_id}-${node.id}`, {
        id: `VIEW-CONTAINS-${lot.business_id}-${node.id}`,
        type: 'CONTAINS', source: lot.id, target: node.id,
      })
      for (const chamberId of chamberNodeId ? [] : chamberIds) {
        relationships.set(`VIEW-PROCESSED-IN-${node.id}-${chamberId}`, {
          id: `VIEW-PROCESSED-IN-${node.id}-${chamberId}`,
          type: 'PROCESSED_IN', source: node.id, target: chamberId,
        })
      }
    }
    if (chamberNodeId) {
      relationships.set(`VIEW-LOT-PROCESSED-IN-${lot.id}-${chamberNodeId}`, {
        id: `VIEW-LOT-PROCESSED-IN-${lot.id}-${chamberNodeId}`,
        type: 'PROCESSED_IN', source: lot.id, target: chamberNodeId,
      })
    }
  }
  const projection = {
    root_node_id: staticGraph.root_node_id ?? normalized.root_node_id,
    nodes: [...nodes.values()],
    relationships: [...relationships.values()],
    graph_revision: normalized.graph_revision,
  }
  if (!selectedWaferId || chamberNodeId) return projection

  // LOT 단독 화면의 배치 축은 유지하면서, 선택 Wafer가 거치지 않은 Chamber와
  // 그에만 연결된 ontology 가지는 제외한다.
  const projectionNodes = new Map(projection.nodes.map((node) => [node.id, node]))
  if (projectionNodes.get(selectedWaferId)?.label !== 'Wafer') return projection
  const retainedNodeIds = new Set([selectedWaferId])
  const retainedRelationships = []
  const include = (relationship) => {
    retainedRelationships.push(relationship)
    retainedNodeIds.add(relationship.source)
    retainedNodeIds.add(relationship.target)
  }
  projection.relationships
    .filter((relationship) => relationship.source === selectedWaferId || relationship.target === selectedWaferId)
    .forEach(include)
  const includeStructuralRelations = (types) => {
    projection.relationships
      .filter((relationship) => types.includes(relationship.type) &&
        (retainedNodeIds.has(relationship.source) || retainedNodeIds.has(relationship.target)))
      .forEach(include)
  }
  includeStructuralRelations(['PART_OF', 'MEASURED_ON'])
  includeStructuralRelations(['PERFORMS', 'OF_MODEL'])
  includeStructuralRelations(['IN_AREA'])
  const relatedStepIds = new Set([...retainedNodeIds].filter((nodeId) => projectionNodes.get(nodeId)?.label === 'ProcessStep'))
  projection.relationships
    .filter((relationship) => relationship.type === 'NEXT_STEP' && relatedStepIds.has(relationship.source) && relatedStepIds.has(relationship.target))
    .forEach(include)

  // filter 뒤 stack이 다시 중앙으로 모이지 않도록, LOT 단독 그래프의 원래 좌표를 사용한다.
  const stablePositions = new Map(layoutOntologyNodes(projection).map(({ node, position }) => [node.id, position]))
  // Wafer 화면에서는 각 처리 Chamber가 자신의 EQP와 같은 수평 축을 공유한다.
  const processedChamberIds = new Set(retainedRelationships
    .filter((relationship) => relationship.type === 'PROCESSED_IN' && relationship.source === selectedWaferId)
    .map((relationship) => relationship.target))
  projection.relationships
    .filter((relationship) => relationship.type === 'PART_OF' && processedChamberIds.has(relationship.source))
    .forEach((relationship) => {
      const chamberPosition = stablePositions.get(relationship.source)
      const equipmentPosition = stablePositions.get(relationship.target)
      if (chamberPosition && equipmentPosition) {
        stablePositions.set(relationship.source, { ...chamberPosition, y: equipmentPosition.y })
      }
    })
  return {
    ...projection,
    root_node_id: [...retainedNodeIds].find((nodeId) => projectionNodes.get(nodeId)?.label === 'Chamber') ?? projection.root_node_id,
    nodes: projection.nodes
      .filter((node) => retainedNodeIds.has(node.id))
      .map((node) => ({ ...node, properties: { ...node.properties, display_position: stablePositions.get(node.id) } })),
    relationships: [...new Map(retainedRelationships.map((relationship) => [relationship.id, relationship])).values()],
  }
}

export function attachWaferAlarmContext(graph, waferNodeId, alarms) {
  const normalized = normalizeOntologyGraph(graph)
  const wafer = nodeById(normalized ?? { nodes: [] }, waferNodeId)
  if (!normalized || wafer?.label !== 'Wafer' || !Array.isArray(alarms) || alarms.length === 0) return normalized
  const nodes = new Map(normalized.nodes.map((node) => [node.id, node]))
  const relationships = new Map(normalized.relationships.map((relationship) => [relationship.id, relationship]))
  for (const sensorId of new Set(alarms.map((alarm) => alarm?.sensor_id).filter(Boolean))) {
    const parameterNodeId = `Parameter:${sensorId}`
    if (nodes.has(parameterNodeId)) {
      relationships.set(`VIEW-ALARM-ON-${wafer.id}-${sensorId}`, {
        id: `VIEW-ALARM-ON-${wafer.id}-${sensorId}`,
        type: 'ALARM_ON', source: wafer.id, target: parameterNodeId,
      })
    }
  }
  return { ...normalized, nodes: [...nodes.values()], relationships: [...relationships.values()] }
}

export function annotateWaferAlarmHints(graph, alarms) {
  const normalized = normalizeOntologyGraph(graph)
  if (!normalized || !Array.isArray(alarms)) return normalized
  const countByHistoryId = new Map()
  for (const alarm of alarms) {
    if (!alarm?.lot_hist_id) continue
    countByHistoryId.set(alarm.lot_hist_id, (countByHistoryId.get(alarm.lot_hist_id) ?? 0) + 1)
  }
  return {
    ...normalized,
    nodes: normalized.nodes.map((node) => {
      if (node.label !== 'Wafer') return node
      const alarmCount = countByHistoryId.get(node.properties?.lot_hist_id) ?? 0
      return { ...node, properties: { ...node.properties, alarm_count: alarmCount } }
    }),
  }
}

export function buildOntologyOverviewLanes(graphs) {
  const merged = mergeOntologyGraphs(graphs)
  if (!merged) return []
  const relatedNodeIds = (type, side, ids, resultSide) => {
    const idSet = new Set(ids)
    return merged.relationships
      .filter((relationship) => relationship.type === type && idSet.has(relationship[side]))
      .map((relationship) => relationship[resultSide])
  }
  const nodesByIds = (ids, label) => {
    const idSet = new Set(ids)
    return sorted(merged.nodes.filter((node) => node.label === label && idSet.has(node.id)))
  }

  const lanes = sorted(merged.nodes.filter((node) => node.label === 'EquipmentModel')).map((model) => {
    const equipmentIds = relatedNodeIds('OF_MODEL', 'target', [model.id], 'source')
    const stepIds = relatedNodeIds('PERFORMS', 'source', equipmentIds, 'target')
    const areaIds = relatedNodeIds('IN_AREA', 'source', stepIds, 'target')
    const chamberIds = relatedNodeIds('PART_OF', 'target', equipmentIds, 'source')
    const parameterIds = relatedNodeIds('MEASURED_ON', 'target', chamberIds, 'source')
    return {
      model,
      steps: nodesByIds(stepIds, 'ProcessStep'),
      areas: nodesByIds(areaIds, 'Area'),
      equipments: nodesByIds(equipmentIds, 'Equipment'),
      chambers: nodesByIds(chamberIds, 'Chamber'),
      parameters: nodesByIds(parameterIds, 'Parameter'),
    }
  })
  return lanes.sort((a, b) => {
    const aStep = Number(a.steps[0]?.properties?.step_seq ?? Number.MAX_SAFE_INTEGER)
    const bStep = Number(b.steps[0]?.properties?.step_seq ?? Number.MAX_SAFE_INTEGER)
    return aStep - bStep || a.model.id.localeCompare(b.model.id)
  })
}

const sorted = (nodes) => [...nodes].sort((a, b) => a.id.localeCompare(b.id))

const stack = (nodes, { x, centerY, gap }) => {
  const ordered = sorted(nodes)
  const startY = centerY - ((ordered.length - 1) * gap) / 2
  return ordered.map((node, index) => ({ node, position: { x, y: startY + index * gap } }))
}

const relationNode = (graph, relationship, side) =>
  graph.nodes.find((node) => node.id === relationship[side])

const ontologyArea = (graph, node) => {
  if (node.label === 'Area') return node.business_id
  if (node.label === 'Equipment') return node.properties?.area ?? null
  if (node.label === 'ProcessStep') {
    return graph.relationships
      .filter((relationship) => relationship.type === 'IN_AREA' && relationship.source === node.id)
      .map((relationship) => relationNode(graph, relationship, 'target'))
      .find((related) => related?.label === 'Area')?.business_id ?? null
  }
  if (node.label === 'EquipmentModel') {
    return graph.relationships
      .filter((relationship) => relationship.type === 'OF_MODEL' && relationship.target === node.id)
      .map((relationship) => relationNode(graph, relationship, 'source'))
      .find((related) => related?.label === 'Equipment')?.properties?.area ?? null
  }
  if (node.label === 'Chamber') {
    const equipment = graph.relationships
      .filter((relationship) => relationship.type === 'PART_OF' && relationship.source === node.id)
      .map((relationship) => relationNode(graph, relationship, 'target'))
      .find((related) => related?.label === 'Equipment')
    return equipment?.properties?.area ?? null
  }
  if (node.label === 'Parameter') {
    const chamber = graph.relationships
      .filter((relationship) => relationship.type === 'MEASURED_ON' && relationship.source === node.id)
      .map((relationship) => relationNode(graph, relationship, 'target'))
      .find((related) => related?.label === 'Chamber')
    return chamber ? ontologyArea(graph, chamber) : null
  }
  if (node.label === 'Wafer') {
    const chamber = graph.relationships
      .filter((relationship) => relationship.type === 'PROCESSED_IN' && relationship.source === node.id)
      .map((relationship) => relationNode(graph, relationship, 'target'))
      .find((related) => related?.label === 'Chamber')
    return chamber ? ontologyArea(graph, chamber) : null
  }
  if (node.label === 'Lot') {
    const wafer = graph.relationships
      .filter((relationship) => relationship.type === 'CONTAINS' && relationship.source === node.id)
      .map((relationship) => relationNode(graph, relationship, 'target'))
      .find((related) => related?.label === 'Wafer')
    return wafer ? ontologyArea(graph, wafer) : null
  }
  return null
}

// 실제 이력은 Chamber 아래의 LOT 묶음으로 둔다. Parameter는 Chamber의 측정 구조이고
// Lot/Wafer는 처리 이력이므로 한 줄로 잇지 않는다. Wafer의 개별 CONTAINS 간선은 Canvas에서
// 숨기고, 실제 wafer node만 밀도 있는 rack으로 표시한다.
const productionContextLayout = (graph, { anchorX, startY, columnsPerRow = null }) => {
  const lotNodes = sorted(graph.nodes.filter((node) => node.label === 'Lot'))
  const waferByLotId = new Map(lotNodes.map((lot) => [lot.id, []]))
  for (const relationship of graph.relationships) {
    if (relationship.type !== 'CONTAINS') continue
    const wafer = relationNode(graph, relationship, 'target')
    if (wafer?.label === 'Wafer' && waferByLotId.has(relationship.source)) {
      waferByLotId.get(relationship.source).push(wafer)
    }
  }

  const tileColumns = 3
  const tileWidth = 860
  const tileHeight = 560
  const defaultWaferColumns = 4
  const waferColumnGap = 150
  const waferRowGap = 76
  const placed = []
  if (lotNodes.length === 1) {
    const lot = lotNodes[0]
    const wafers = sorted(waferByLotId.get(lot.id) ?? [])
    // LOT incident에서는 한 줄에 wafer node를 최대 두 개씩만 두고 아래로 이어 간다.
    // 수량은 고정하지 않고 현재 선택된 LOT·Chamber의 실데이터만 배치한다.
    const waferColumns = Math.max(1, Math.min(columnsPerRow ?? defaultWaferColumns, wafers.length || 1))
    const gridWidth = (waferColumns - 1) * waferColumnGap + 132
    // ALARM badge가 붙은 Wafer는 기본 node보다 높아진다. 다음 행과 겹치지 않도록
    // 해당 LOT rack 전체의 row 간격을 넉넉히 잡는다.
    const rowGap = wafers.some((wafer) => Number(wafer.properties?.alarm_count ?? 0) > 0) ? 108 : waferRowGap
    placed.push({ node: lot, position: { x: anchorX - 15, y: startY } })
    for (const [waferIndex, wafer] of wafers.entries()) {
      placed.push({
        node: wafer,
        position: {
          x: anchorX + 75 - gridWidth / 2 + (waferIndex % waferColumns) * waferColumnGap,
          y: startY + 170 + Math.floor(waferIndex / waferColumns) * rowGap,
        },
      })
    }
    return placed
  }
  for (const [index, lot] of lotNodes.entries()) {
    const tileX = anchorX - 280 + (index % tileColumns) * tileWidth
    const tileY = startY + Math.floor(index / tileColumns) * tileHeight
    placed.push({ node: lot, position: { x: tileX + 205, y: tileY } })
    for (const [waferIndex, wafer] of sorted(waferByLotId.get(lot.id) ?? []).entries()) {
      placed.push({
        node: wafer,
        position: {
          x: tileX + (waferIndex % defaultWaferColumns) * waferColumnGap,
          y: tileY + 144 + Math.floor(waferIndex / defaultWaferColumns) * waferRowGap,
        },
      })
    }
  }
  return placed
}

const fullOntologyLayout = (graph) => {
  const placed = []
  for (const [area, laneY] of [['Photo', 260], ['Etch', 900]]) {
    const laneNodes = graph.nodes.filter((node) => ontologyArea(graph, node) === area)
    const byLabel = Object.fromEntries(
      ONTOLOGY_NODE_LABELS.map((label) => [label, laneNodes.filter((node) => node.label === label)]),
    )
    placed.push(...stack(byLabel.EquipmentModel, { x: 0, centerY: laneY, gap: 150 }))
    placed.push(...stack(byLabel.ProcessStep, { x: 230, centerY: laneY, gap: 150 }))
    placed.push(...stack(byLabel.Area, { x: 460, centerY: laneY, gap: 150 }))
    placed.push(...stack(byLabel.Equipment, { x: 690, centerY: laneY, gap: 150 }))
    placed.push(
      ...stack(byLabel.Chamber, { x: 920, centerY: laneY, gap: 100 }).map((item) => ({
        ...item,
        root: item.node.id === graph.root_node_id,
      })),
    )
    placed.push(...stack(byLabel.Parameter, { x: 1160, centerY: laneY, gap: 125 }))
  }
  placed.push(...productionContextLayout(graph, { anchorX: 920, startY: 1380 }))
  return placed
}

const lotIncidentLayout = (graph) => {
  const root = graph.nodes.find((node) => node.id === graph.root_node_id)
  const byLabel = Object.fromEntries(
    ONTOLOGY_NODE_LABELS.map((label) => [
      label,
      graph.nodes.filter((node) => node.label === label && node.id !== root?.id),
    ]),
  )
  const placed = []
  // LOT 선택 상태는 구조의 기준점(EQP·Chamber)을 같은 축에 두고, 그 아래에
  // incident 단위(LOT·Wafer)를 내려서 읽는다.
  placed.push(...stack(byLabel.EquipmentModel, { x: 560, centerY: 230, gap: 90 }))
  placed.push(...stack(byLabel.ProcessStep, { x: 230, centerY: 70, gap: 120 }))
  placed.push(...stack(byLabel.Area, { x: 230, centerY: 230, gap: 90 }))
  placed.push(...stack(byLabel.Equipment, { x: 560, centerY: 70, gap: 120 }))
  if (root) placed.push({ node: root, position: { x: 920, y: 70 }, root: true })
  placed.push(...stack(byLabel.Parameter, { x: 1400, centerY: 230, gap: 100 }))
  placed.push(...productionContextLayout(graph, { anchorX: 920, startY: 230, columnsPerRow: 3 }))
  return placed
}

// LOT 우선 보기에서는 실제 방문 Chamber의 ontology subgraph를 Photo/Etch 두 lane에
// 압축하고, LOT 처리 이력은 하단의 별도 레인에서 읽는다.
const lotRouteLayout = (graph) => {
  const placed = []
  const hasWafer = graph.nodes.some((node) => node.label === 'Wafer')
  // 선택 Wafer 화면은 한 경로만 남으므로, LOT 단독 화면보다 조금 조밀하게 둔다.
  const parameterGap = hasWafer ? 90 : 120
  const parameterCount = (area) => graph.nodes.filter((node) => node.label === 'Parameter' && ontologyArea(graph, node) === area).length
  const photoParameterSpan = Math.max(0, parameterCount('Photo') - 1) * parameterGap / 2
  const etchParameterSpan = Math.max(0, parameterCount('Etch') - 1) * parameterGap / 2
  // LOT 단독 화면은 Parameter 수에 맞춰 Etch 레인 전체를 아래로 내려, 같은
  // PARAMETER 열(x=1450) 안에서도 Photo/Etch card가 겹치지 않게 한다.
  const etchLaneY = Math.max(hasWafer ? 440 : 510, 150 + photoParameterSpan + etchParameterSpan + (hasWafer ? 72 : 90))
  for (const [area, laneY] of [['Photo', 150], ['Etch', etchLaneY]]) {
    const laneNodes = graph.nodes.filter((node) => ontologyArea(graph, node) === area)
    const byLabel = Object.fromEntries(
      ONTOLOGY_NODE_LABELS.map((label) => [label, laneNodes.filter((node) => node.label === label)]),
    )
    placed.push(...stack(byLabel.EquipmentModel, { x: 780, centerY: laneY + 140, gap: 120 }))
    placed.push(...stack(byLabel.ProcessStep, { x: 480, centerY: laneY, gap: 120 }))
    placed.push(...stack(byLabel.Area, { x: 220, centerY: laneY, gap: 120 }))
    placed.push(...stack(byLabel.Equipment, { x: 780, centerY: laneY, gap: 150 }))
    placed.push(...stack(byLabel.Chamber, { x: 1120, centerY: laneY, gap: 120 }).map((item) => ({
      ...item,
      root: item.node.id === graph.root_node_id,
    })))
    placed.push(...stack(byLabel.Parameter, { x: 1450, centerY: laneY, gap: parameterGap }))
  }
  const lots = sorted(graph.nodes.filter((node) => node.label === 'Lot'))
  const wafers = graph.nodes.filter((node) => node.label === 'Wafer')
  // LOT trunk는 Equipment → Chamber 구성 관계의 중앙 축과 가깝게 둔다.
  const isWaferRoute = wafers.length === 1
  placed.push(...lots.map((node, index) => ({
    node,
    position: isWaferRoute ? { x: 930, y: -180 } : { x: 860 + index * 260, y: -80 },
  })))
  // Wafer 단독 선택은 LOT 바로 아래에서 시작해, 선택 Wafer의 Chamber 경로로 이어진다.
  placed.push(...stack(wafers, isWaferRoute ? { x: 954, centerY: -55, gap: 110 } : { x: 1100, centerY: -80, gap: 110 }))
  return placed
}

// Chamber를 선택한 직후에는 실제 처리 이력을 LOT 단위로만 요약한다. LOT을 모두
// 그래프 하단의 한 줄에 놓아, 선택 Chamber를 거친 LOT의 범위를 한눈에 확인하게 한다.
const chamberLotSummaryLayout = (graph) => {
  const root = graph.nodes.find((node) => node.id === graph.root_node_id)
  const chamberNodes = graph.nodes.filter((node) => node.label === 'Chamber')
  const byLabel = Object.fromEntries(
    ONTOLOGY_NODE_LABELS.map((label) => [
      label,
      graph.nodes.filter((node) => node.label === label && node.id !== graph.root_node_id),
    ]),
  )
  const placed = []
  const currentStepId = graph.relationships.find((relationship) => relationship.type === 'PERFORMS')?.target
  const currentStep = byLabel.ProcessStep.find((node) => node.id === currentStepId) ?? sorted(byLabel.ProcessStep)[0]
  const adjacentSteps = byLabel.ProcessStep.filter((node) => node.id !== currentStep?.id)

  placed.push(...stack(byLabel.EquipmentModel, { x: 0, centerY: 70, gap: 150 }))
  if (currentStep) placed.push({ node: currentStep, position: { x: 230, y: 215 } })
  placed.push(...stack(adjacentSteps, { x: 230, centerY: 390, gap: 150 }))
  placed.push(...stack(byLabel.Area, { x: 460, centerY: 500, gap: 150 }))
  placed.push(...stack(byLabel.Equipment, { x: 690, centerY: 70, gap: 170 }))
  placed.push(
    ...stack(chamberNodes, { x: 920, centerY: 330, gap: 200 }).map((item) => ({
      ...item,
      root: item.node.id === root?.id,
    })),
  )
  placed.push(...stack(byLabel.Parameter, { x: 1400, centerY: 290, gap: 145 }))
  // 1열 좌→우 배치: 선택 Chamber 바로 아래를 중심으로 놓고, LOT 수가 많아도
  // 줄바꿈하지 않는다. Canvas pan/zoom으로 전체 LOT을 탐색한다.
  const lots = sorted(byLabel.Lot)
  const lotGap = 210
  const lotStartX = 920 - ((lots.length - 1) * lotGap) / 2
  placed.push(...lots.map((node, index) => ({
    node,
    position: { x: lotStartX + index * lotGap, y: 620 },
  })))
  return placed
}

export function layoutOntologyNodes(graph) {
  const normalized = normalizeOntologyGraph(graph)
  if (!normalized) return []
  const hasFixedPositions = normalized.nodes.length > 0 && normalized.nodes.every((node) => {
    const position = node.properties?.display_position
    return Number.isFinite(position?.x) && Number.isFinite(position?.y)
  })
  if (hasFixedPositions) {
    return normalized.nodes.map((node) => ({
      node,
      position: node.properties.display_position,
      root: node.id === normalized.root_node_id,
    }))
  }
  if (normalized.nodes.some((node) => node.label === 'Lot')) {
    if (
      !normalized.nodes.some((node) => node.label === 'Wafer') &&
      normalized.nodes.filter((node) => node.label === 'Chamber').some((node) => node.properties?.lot_route_order != null)
    ) {
      return lotRouteLayout(normalized)
    }
    if (
      normalized.nodes.some((node) => node.label === 'Wafer') &&
      normalized.nodes.filter((node) => node.label === 'Chamber').some((node) => node.properties?.lot_route_order != null)
    ) {
      return lotRouteLayout(normalized)
    }
    if (!normalized.nodes.some((node) => node.label === 'Wafer')) {
      return chamberLotSummaryLayout(normalized)
    }
    return lotIncidentLayout(normalized)
  }
  if (normalized.nodes.filter((node) => node.label === 'EquipmentModel').length > 1) {
    return fullOntologyLayout(normalized)
  }
  const root = normalized.nodes.find((node) => node.id === normalized.root_node_id)
  const chamberNodes = normalized.nodes.filter((node) => node.label === 'Chamber')
  const byLabel = Object.fromEntries(
    ONTOLOGY_NODE_LABELS.map((label) => [
      label,
      normalized.nodes.filter((node) => node.label === label && node.id !== normalized.root_node_id),
    ]),
  )
  const placed = []
  // 발표용 읽기 순서는 Model → Step → Area → Equipment → Chamber → Parameter다.
  // Neo4j relationship source/target은 바꾸지 않고 화면 위치만 왼쪽에서 오른쪽으로 정렬한다.
  const currentStepId = normalized.relationships.find((relationship) => relationship.type === 'PERFORMS')?.target
  const currentStep = byLabel.ProcessStep.find((node) => node.id === currentStepId) ?? sorted(byLabel.ProcessStep)[0]
  const adjacentSteps = byLabel.ProcessStep.filter((node) => node.id !== currentStep?.id)

  placed.push(...stack(byLabel.EquipmentModel, { x: 0, centerY: 70, gap: 150 }))
  if (currentStep) placed.push({ node: currentStep, position: { x: 230, y: 215 } })
  placed.push(...stack(adjacentSteps, { x: 230, centerY: 390, gap: 150 }))
  placed.push(...stack(byLabel.Area, { x: 460, centerY: 500, gap: 150 }))
  placed.push(...stack(byLabel.Equipment, { x: 690, centerY: 70, gap: 170 }))
  placed.push(
    ...stack(chamberNodes, { x: 920, centerY: 330, gap: 200 }).map((item) => ({
      ...item,
      root: item.node.id === root?.id,
    })),
  )
  placed.push(...stack(byLabel.Parameter, { x: 1400, centerY: 290, gap: 145 }))
  placed.push(...productionContextLayout(normalized, { anchorX: 920, startY: 520 }))
  return placed
}

export function orientOntologyRelationships(graph, layout, { chamberOnly = false } = {}) {
  const normalized = normalizeOntologyGraph(graph)
  const xByNodeId = new Map(layout.map(({ node, position }) => [node.id, position.x]))
  const nodeById = new Map((normalized?.nodes ?? []).map((node) => [node.id, node]))
  // LOT 단독 route와 LOT+Chamber incident만 전용 ontology 배치 규칙을 적용한다.
  // Chamber 선택의 LOT 요약 행은 기존 layout을 유지한다.
  const lotRouteOnly = (normalized?.nodes ?? []).some((node) => node.properties?.lot_route_order != null)
  const waferRoute = lotRouteOnly && (normalized?.nodes ?? []).some((node) => node.label === 'Wafer')
  const lotIncident = (normalized?.nodes ?? []).some((node) => node.label === 'Lot') &&
    (normalized?.nodes ?? []).some((node) => node.label === 'Wafer') && !waferRoute
  return (normalized?.relationships ?? []).map((relationship) => {
    const sourceNode = nodeById.get(relationship.source)
    const targetNode = nodeById.get(relationship.target)
    if (lotIncident && relationship.type === 'IN_AREA') {
      return {
        ...relationship,
        display_source: relationship.source,
        display_target: relationship.target,
        display_reversed: false,
        display_vertical: true,
      }
    }
    if (lotRouteOnly && relationship.type === 'IN_AREA') {
      return {
        ...relationship,
        display_source: relationship.target,
        display_target: relationship.source,
        display_reversed: true,
        display_straight: waferRoute,
      }
    }
    if (waferRoute && relationship.type === 'NEXT_STEP') {
      return {
        ...relationship,
        display_source: relationship.source,
        display_target: relationship.target,
        display_reversed: false,
        display_vertical: true,
      }
    }
    if ((lotRouteOnly || lotIncident) && relationship.type === 'OF_MODEL') {
      return {
        ...relationship,
        display_source: relationship.source,
        display_target: relationship.target,
        display_reversed: false,
        display_vertical: true,
      }
    }
    if (chamberOnly && relationship.type === 'NEXT_STEP') {
      return {
        ...relationship,
        display_source: relationship.source,
        display_target: relationship.target,
        display_reversed: false,
        display_vertical: true,
      }
    }
    // 화면에서는 Chamber에서 처리 이력 묶음으로 내려가 읽히게 한다. 원본 관계의 endpoint나
    // direction은 바꾸지 않고, presentation 방향과 역방향 label만 바꾼다.
    if (
      relationship.type === 'PROCESSED_IN' &&
      sourceNode?.label === 'Lot' &&
      targetNode?.label === 'Chamber' &&
      targetNode.properties?.lot_route_order == null
    ) {
      return {
        ...relationship,
        display_source: relationship.target,
        display_target: relationship.source,
        display_reversed: true,
        display_vertical: true,
      }
    }
    if (relationship.type === 'CONTAINS' && sourceNode?.label === 'Lot' && targetNode?.label === 'Wafer') {
      return {
        ...relationship,
        display_source: relationship.source,
        display_target: relationship.target,
        display_reversed: false,
        display_vertical: true,
      }
    }
    const reversed = (xByNodeId.get(relationship.source) ?? 0) > (xByNodeId.get(relationship.target) ?? 0)
    return {
      ...relationship,
      display_source: reversed ? relationship.target : relationship.source,
      display_target: reversed ? relationship.source : relationship.target,
      display_reversed: reversed,
    }
  })
}
