export const ONTOLOGY_NODE_LABELS = Object.freeze([
  'Chamber',
  'Equipment',
  'EquipmentModel',
  'Area',
  'ProcessStep',
  'Parameter',
])

export const ONTOLOGY_NODE_META = Object.freeze({
  Area: { shortLabel: 'AREA', color: '#0f766e' },
  Chamber: { shortLabel: 'CHAMBER', color: '#2563eb' },
  Equipment: { shortLabel: 'EQP', color: '#16a34a' },
  EquipmentModel: { shortLabel: 'MODEL', color: '#7c3aed' },
  Parameter: { shortLabel: 'PARAM', color: '#0ea5e9' },
  ProcessStep: { shortLabel: 'STEP', color: '#f59e0b' },
})

export const ONTOLOGY_RELATION_LABELS = Object.freeze({
  IN_AREA: '공정 영역',
  MEASURED_ON: '측정 챔버',
  NEXT_STEP: '다음 공정',
  OF_MODEL: '설비 모델',
  PART_OF: '소속 설비',
  PERFORMS: '수행 공정',
})

export const ONTOLOGY_REVERSED_RELATION_LABELS = Object.freeze({
  MEASURED_ON: '측정 파라미터',
  OF_MODEL: '적용 설비',
  PART_OF: '구성 챔버',
  PERFORMS: '공정 수행 설비',
})

// Neo4j node properties 전체를 화면에 노출하지 않는다. 식별자·표시명에 필요한 공개 키만 허용한다.
export const PUBLIC_NODE_PROPERTIES = Object.freeze({
  Area: Object.freeze(['area_id', 'area_name']),
  Chamber: Object.freeze(['chamber_id']),
  Equipment: Object.freeze(['equipment_id', 'equipment_name']),
  EquipmentModel: Object.freeze(['model_code', 'model_name']),
  Parameter: Object.freeze(['parameter_id']),
  ProcessStep: Object.freeze(['step_id', 'step_name', 'step_seq']),
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
  return null
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
  return placed
}

export function layoutOntologyNodes(graph) {
  const normalized = normalizeOntologyGraph(graph)
  if (!normalized) return []
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
  placed.push(...stack(byLabel.Parameter, { x: 1160, centerY: 290, gap: 145 }))
  return placed
}

export function orientOntologyRelationships(graph, layout) {
  const normalized = normalizeOntologyGraph(graph)
  const xByNodeId = new Map(layout.map(({ node, position }) => [node.id, position.x]))
  return (normalized?.relationships ?? []).map((relationship) => {
    const reversed = (xByNodeId.get(relationship.source) ?? 0) > (xByNodeId.get(relationship.target) ?? 0)
    return {
      ...relationship,
      display_source: reversed ? relationship.target : relationship.source,
      display_target: reversed ? relationship.source : relationship.target,
      display_reversed: reversed,
    }
  })
}
