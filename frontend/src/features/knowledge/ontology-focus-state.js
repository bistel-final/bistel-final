import { normalizeOntologyGraph } from '../../shared/graph/ontology-graph.js'

const valueOf = (params, key) => params.get(key)?.trim() ?? ''

const nodeIdsOf = (params, key) => [...new Set(valueOf(params, key).split(',').map((value) => value.trim()).filter(Boolean))]

export function parseOntologyFocus(params) {
  const chamberId = valueOf(params, 'chamber_id')
  const relationId = valueOf(params, 'relation_id')
  const graphRevision = valueOf(params, 'graph_revision')
  const directNodeIds = nodeIdsOf(params, 'direct_node_ids')
  const checkNodeIds = nodeIdsOf(params, 'check_node_ids')
  const hasImpactNodes = directNodeIds.length + checkNodeIds.length > 0
  const supplied = [chamberId, relationId, graphRevision, hasImpactNodes].filter(Boolean).length
  if (supplied === 0) return { phase: 'none' }
  if (hasImpactNodes) {
    if (!chamberId || !graphRevision || relationId || directNodeIds.length + checkNodeIds.length > 20) {
      return {
        phase: 'invalid',
        message: '영향 범위 링크에는 chamber_id·graph_revision과 1~20개의 영향 node ID가 필요합니다.',
      }
    }
    return { phase: 'ready', kind: 'impact', chamberId, graphRevision, directNodeIds, checkNodeIds }
  }
  if (supplied !== 3) {
    return {
      phase: 'invalid',
      message: '온톨로지 링크에 chamber_id·relation_id·graph_revision이 모두 필요합니다.',
    }
  }
  return { phase: 'ready', kind: 'relation', chamberId, relationId, graphRevision }
}

export function resolveOntologyFocus(graph, focus) {
  if (focus.phase !== 'ready') return focus
  const normalized = normalizeOntologyGraph(graph)
  if (!normalized) return { ...focus, phase: 'not-found' }
  if (normalized.graph_revision !== focus.graphRevision) {
    return {
      ...focus,
      phase: 'revision-mismatch',
      actualRevision: normalized.graph_revision,
    }
  }
  if (focus.kind === 'impact') {
    const byId = new Map(normalized.nodes.map((item) => [item.id, item]))
    const directNodes = focus.directNodeIds.map((id) => byId.get(id)).filter(Boolean)
    const checkNodes = focus.checkNodeIds.map((id) => byId.get(id)).filter(Boolean)
    if (directNodes.length !== focus.directNodeIds.length || checkNodes.length !== focus.checkNodeIds.length) {
      return { ...focus, phase: 'not-found' }
    }
    return { ...focus, phase: 'found', directNodes, checkNodes, graph: normalized }
  }
  const relation = normalized.relationships.find((item) => item.id === focus.relationId)
  if (!relation) return { ...focus, phase: 'not-found' }
  const source = normalized.nodes.find((item) => item.id === relation.source) ?? null
  const target = normalized.nodes.find((item) => item.id === relation.target) ?? null
  return { ...focus, phase: 'found', relation, source, target, graph: normalized }
}
