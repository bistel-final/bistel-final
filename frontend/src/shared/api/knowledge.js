import apiClient, { mockEnabledFor, mockResponse } from './client.js'
import { assertExactObject, compactParams, requireNonEmptyString } from './contract.js'
import { CORE_CHAMBER_GRAPH, CORE_DOCUMENT_HIT } from './contractMocks.js'
import { DOC_DB, DOC_SCORES } from '../../features/knowledge/mock/documents.js'
import { RELATIONS } from '../../features/knowledge/mock/relations.js'

const USE_MOCK = mockEnabledFor('KNOWLEDGE')

const mockGraphNode = (label, businessId, displayName = businessId, properties = {}) => ({
  node_id: `${label}:${businessId}`,
  label,
  business_id: businessId,
  name: displayName,
  properties,
})

const mockGraphRelationship = (relation_id, type, from_node_id, to_node_id) => ({
  relation_id,
  type,
  from_node_id,
  to_node_id,
})

const mockCoreGraphFor = (chamberId) => {
  if (chamberId === CORE_CHAMBER_GRAPH.context.chamber_id) return CORE_CHAMBER_GRAPH
  const equipmentId = chamberId.replace(/-PM\d+$/, '')
  const equipmentNumber = Number(equipmentId.replace('EQP', ''))
  const isPhoto = equipmentNumber <= 3
  const area = isPhoto ? 'Photo' : 'Etch'
  const areaName = isPhoto ? 'Photolithography' : 'Dry Etch'
  const modelCode = isPhoto ? 'PH-9000' : 'ET-7500'
  const modelName = isPhoto ? 'Photo Scanner' : 'Dry Etcher'
  const processStepId = isPhoto ? 'CT-PHOTO' : 'CT-ETCH'
  const adjacentStepId = isPhoto ? 'CT-ETCH' : 'CT-PHOTO'
  const parameters = isPhoto ? ['PH_DEV', 'PH_DOSE', 'PH_FOCUS', 'PH_PEB'] : ['ET_PRES', 'ET_REFL', 'ET_CF4', 'ET_ESC']
  const focusParameter = isPhoto ? 'PH_FOCUS' : 'ET_REFL'
  const focusRelationId = CORE_CHAMBER_GRAPH.relationships.find(
    (relationship) => relationship.from_node_id === 'Parameter:PH_FOCUS',
  ).relation_id
  const siblingChamber = `${equipmentId}-${chamberId.endsWith('PM1') ? 'PM2' : 'PM1'}`
  const rootId = `Chamber:${chamberId}`
  const equipmentNodeId = `Equipment:${equipmentId}`
  const currentStepId = `ProcessStep:${processStepId}`
  const adjacentNodeId = `ProcessStep:${adjacentStepId}`
  const measuredRelationships = parameters.map((parameterId, index) =>
    mockGraphRelationship(
      parameterId === focusParameter ? focusRelationId : `MOCK-${chamberId}-MEASURED-${index + 1}`,
      'MEASURED_ON',
      `Parameter:${parameterId}`,
      rootId,
    ),
  )
  const structuralRelationships = [
    mockGraphRelationship(`MOCK-${chamberId}-PART`, 'PART_OF', rootId, equipmentNodeId),
    mockGraphRelationship(`MOCK-${siblingChamber}-PART`, 'PART_OF', `Chamber:${siblingChamber}`, equipmentNodeId),
    mockGraphRelationship(`MOCK-${equipmentId}-MODEL`, 'OF_MODEL', equipmentNodeId, `EquipmentModel:${modelCode}`),
    mockGraphRelationship(`MOCK-${equipmentId}-STEP`, 'PERFORMS', equipmentNodeId, currentStepId),
    mockGraphRelationship(`MOCK-${processStepId}-AREA`, 'IN_AREA', currentStepId, `Area:${area}`),
    mockGraphRelationship(
      `MOCK-${processStepId}-ADJACENT`,
      'NEXT_STEP',
      isPhoto ? currentStepId : adjacentNodeId,
      isPhoto ? adjacentNodeId : currentStepId,
    ),
  ]
  const relationships = [...measuredRelationships, ...structuralRelationships]
  const nodes = [
    mockGraphNode('Chamber', chamberId, chamberId, { chamber_id: chamberId }),
    mockGraphNode('Chamber', siblingChamber, siblingChamber, { chamber_id: siblingChamber }),
    mockGraphNode('Equipment', equipmentId, equipmentId, { equipment_id: equipmentId, area, model_code: modelCode }),
    mockGraphNode('EquipmentModel', modelCode, modelName, { model_code: modelCode, model_name: modelName }),
    mockGraphNode('Area', area, areaName, { area_id: area, area_name: areaName }),
    mockGraphNode('ProcessStep', processStepId, processStepId, { step_id: processStepId, step_seq: isPhoto ? 1 : 2 }),
    mockGraphNode('ProcessStep', adjacentStepId, adjacentStepId, { step_id: adjacentStepId, step_seq: isPhoto ? 2 : 1 }),
    ...parameters.map((parameterId) =>
      mockGraphNode('Parameter', parameterId, parameterId, { parameter_id: parameterId }),
    ),
  ]
  return {
    context: {
      area,
      equipment_id: equipmentId,
      chamber_id: chamberId,
      model_code: modelCode,
      process_step_id: processStepId,
      adjacent_process_step_ids: [adjacentStepId],
      parameter_ids: parameters,
      relation_ids: relationships.map((relationship) => relationship.relation_id),
    },
    root_node_id: rootId,
    graph_revision: CORE_CHAMBER_GRAPH.graph_revision,
    nodes,
    relationships,
    node_count: nodes.length,
    relationship_count: relationships.length,
  }
}

const MOCK_DOCUMENT_ALIASES = Object.freeze({
  'DOC-TROUBLE-FDC': { legacy: 'TROUBLE_FDC_FaultGuide', firstChunk: 6 },
  'DOC-SPEC-PH9000': { legacy: 'SPEC_PH-9000_PhotoScanner', firstChunk: 5 },
  'DOC-SPEC-ET7500': { legacy: 'SPEC_ET-7500_DryEtcher', firstChunk: 1 },
})

const equipmentNode = (equipment) => ({
  equipment_id: equipment.id,
  equipment_name: equipment.id,
  model_code: equipment.model,
  area_id: equipment.group === 'pho' ? 'PHOTO' : 'ETCH',
  step_id: equipment.group === 'pho' ? 'PHOTO' : 'ETCH',
})

const chamberNode = (chamber) => {
  const equipmentId = chamber.name.replace(/-C\d+$/, '')
  const equipment = RELATIONS.equipments.find((item) => item.id === equipmentId)
  return {
    chamber_id: chamber.name,
    equipment_id: equipmentId,
    chamber_no: Number(chamber.name.match(/-C(\d+)$/)?.[1] ?? 0) || null,
    model_code: equipment?.model ?? null,
    area_id: equipment?.group === 'pho' ? 'PHOTO' : 'ETCH',
    step_id: equipment?.group === 'pho' ? 'PHOTO' : 'ETCH',
  }
}

const chamberRelation = (chamberId) => {
  const rawChamber = RELATIONS.chambers.find((item) => item.name === chamberId)
  if (!rawChamber) return null
  const rawEquipment = RELATIONS.equipments.find((item) => item.id === chamberId.replace(/-C\d+$/, ''))
  const equipment = equipmentNode(rawEquipment)
  const isUpstream = RELATIONS.edge.from === equipment.equipment_id
  const upstream = isUpstream
    ? []
    : RELATIONS.equipments.filter((item) => item.id === RELATIONS.edge.from).map(equipmentNode)
  const downstream = isUpstream
    ? RELATIONS.equipments.filter((item) => item.id === RELATIONS.edge.to).map(equipmentNode)
    : []
  return {
    chamber: chamberNode(rawChamber),
    equipment,
    area: { area_id: equipment.area_id, area_name: equipment.area_id },
    step: { step_id: equipment.step_id, step_name: equipment.step_id, step_seq: isUpstream ? 1 : 2, layer: null },
    sibling_chambers: RELATIONS.chambers
      .filter((item) => item.name !== chamberId && item.name.replace(/-C\d+$/, '') === equipment.equipment_id)
      .map(chamberNode),
    upstream,
    downstream,
  }
}

export function getChamberRelationsCore(chamberId, params = {}) {
  const normalizedId = requireNonEmptyString(chamberId, 'chamber_id')
  assertExactObject(params, ['label', 'limit'], 'getChamberRelationsCore params')
  const query = compactParams(params)
  if (USE_MOCK) return mockResponse(mockCoreGraphFor(normalizedId))
  return apiClient
    .get(`/relations/chambers/${encodeURIComponent(normalizedId)}`, { params: query })
    .then((response) => response.data)
}

const legacyRelationProjection = (graph) => ({
  chamber: { chamber_id: graph.context.chamber_id },
  equipment: { equipment_id: graph.context.equipment_id, model_code: graph.context.model_code },
  area: { area_id: graph.context.area.toUpperCase() },
  step: { step_id: graph.context.process_step_id, layer: null },
  sibling_chambers: graph.nodes
    .filter((node) => node.label === 'Chamber' && node.business_id !== graph.context.chamber_id)
    .map((node) => ({ chamber_id: node.business_id })),
  upstream: [],
  downstream: [],
})

const DOCUMENT_TYPE_BY_ID = Object.freeze({
  'DOC-TROUBLE-FDC': 'TROUBLESHOOT',
  TROUBLE_FDC_FaultGuide: 'TROUBLESHOOT',
  'DOC-SPEC-PH9000': 'SPEC',
  'SPEC_PH-9000_PhotoScanner': 'SPEC',
  'DOC-SPEC-ET7500': 'SPEC',
  'SPEC_ET-7500_DryEtcher': 'SPEC',
})

// Deprecated page projection. B migrates to the raw graph contract in its own Task.
export function getChamberRelations(chamberId) {
  if (USE_MOCK) return mockResponse(chamberRelation(chamberId))
  return getChamberRelationsCore(chamberId).then(legacyRelationProjection)
}

export function getEquipmentRelations(equipmentId) {
  if (USE_MOCK) {
    const rawEquipment = RELATIONS.equipments.find((item) => item.id === equipmentId)
    if (!rawEquipment) return mockResponse(null)
    const firstChamber = RELATIONS.chambers.find((item) => item.name.replace(/-C\d+$/, '') === equipmentId)
    const relation = chamberRelation(firstChamber.name)
    return mockResponse({
      equipment: relation.equipment,
      chambers: RELATIONS.chambers
        .filter((item) => item.name.replace(/-C\d+$/, '') === equipmentId)
        .map(chamberNode),
      area: relation.area,
      step: relation.step,
      upstream: relation.upstream,
      downstream: relation.downstream,
    })
  }
  return apiClient.get(`/relations/equipment/${equipmentId}`).then((response) => response.data)
}

const documentHit = (document, index) => ({
  chunk_id: `CHK-MOCK-${String(index + 1).padStart(4, '0')}`,
  document_id: document.doc,
  doc_type: DOCUMENT_TYPE_BY_ID[document.doc] ?? null,
  title: document.doc,
  section: document.section ?? null,
  score: DOC_SCORES[index] ?? 0,
  content: document.excerpt ?? '',
  model_code: document.model ?? null,
})

export function searchDocuments({ query, model_code, doc_type, top_k = 4 }) {
  const normalizedModelCode = model_code === '전체' ? undefined : model_code
  const normalizedDocType = doc_type === '전체' ? undefined : doc_type
  if (USE_MOCK) {
    const hits = (DOC_DB[query] ?? [])
      .map(documentHit)
      .filter(
        (document) =>
          !normalizedModelCode || document.model_code === 'COMMON' || document.model_code === normalizedModelCode,
      )
      .filter((document) => !normalizedDocType || document.doc_type === normalizedDocType)
      .slice(0, top_k)
    return mockResponse({ query, hits, count: hits.length })
  }
  return apiClient
    .post('/documents/search', { query, model_code: normalizedModelCode, doc_type: normalizedDocType, top_k })
    .then((response) => ({
      query,
      hits: response.data,
      count: response.data.length,
    }))
}

export function searchDocumentsCore(input) {
  assertExactObject(input, ['query', 'model_code', 'top_k'], 'searchDocumentsCore input')
  const query = requireNonEmptyString(input.query, 'query')
  const model_code = input.model_code === undefined ? undefined : requireNonEmptyString(input.model_code, 'model_code')
  const top_k = input.top_k ?? 4
  if (!Number.isInteger(top_k) || top_k < 1 || top_k > 10) throw new TypeError('top_k must be an integer from 1 to 10')
  const body = compactParams({ query, model_code, top_k })
  if (USE_MOCK) return mockResponse([CORE_DOCUMENT_HIT])
  return apiClient.post('/documents/search', body).then((response) => response.data)
}

export function getDocument(documentId) {
  if (USE_MOCK) {
    const alias = MOCK_DOCUMENT_ALIASES[documentId] ?? { legacy: documentId, firstChunk: 1 }
    const seen = new Set()
    const chunks = Object.values(DOC_DB)
      .flat()
      .filter((document) => document.doc === alias.legacy)
      .filter((document) => {
        const key = `${document.section ?? ''}\u0000${document.excerpt ?? ''}`
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })
      .map((document, index) => ({
        chunk_id: `${documentId}:cs2:${String(alias.firstChunk + index).padStart(4, '0')}`,
        chunk_seq: index + 1,
        section_title: document.section ?? null,
        content: document.excerpt ?? '',
      }))
    return mockResponse(
      chunks.length
        ? {
            document_id: documentId,
            title: documentId,
            doc_type: null,
            model_code: null,
            source_path: null,
            version: null,
            chunks,
          }
        : null,
    )
  }
  return apiClient.get(`/documents/${encodeURIComponent(documentId)}`).then((response) => response.data)
}
