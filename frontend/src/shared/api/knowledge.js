import apiClient, { USE_MOCK, mockResponse } from './client.js'
import { DOC_DB, DOC_SCORES } from '../../features/knowledge/mock/documents.js'
import { RELATIONS } from '../../features/knowledge/mock/relations.js'

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

export function getChamberRelations(chamberId) {
  if (USE_MOCK) return mockResponse(chamberRelation(chamberId))
  return apiClient.get(`/relations/chambers/${chamberId}`).then((response) => response.data)
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
  title: document.doc,
  section: document.section ?? null,
  score: DOC_SCORES[index] ?? 0,
  content: document.excerpt ?? '',
  model_code: document.model ?? null,
})

export function searchDocuments({ query, model_code, top_k = 4 }) {
  const normalizedModelCode = model_code === '전체' ? undefined : model_code
  if (USE_MOCK) {
    const hits = (DOC_DB[query] ?? [])
      .map(documentHit)
      .filter(
        (document) =>
          !normalizedModelCode || document.model_code === 'COMMON' || document.model_code === normalizedModelCode,
      )
      .slice(0, top_k)
    return mockResponse({ query, hits, count: hits.length })
  }
  return apiClient
    .post('/documents/search', { query, model_code: normalizedModelCode, top_k })
    .then((response) => response.data)
}

export function getDocument(documentId) {
  if (USE_MOCK) {
    const chunks = Object.values(DOC_DB)
      .flat()
      .filter((document) => document.doc === documentId)
      .map((document, index) => ({
        chunk_id: `CHK-MOCK-${String(index + 1).padStart(4, '0')}`,
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
  return apiClient.get(`/documents/${documentId}`).then((response) => response.data)
}
