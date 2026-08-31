import { normalizeOntologyGraph } from '../../shared/graph/ontology-graph.js'

const valueOf = (params, key) => params.get(key)?.trim() ?? ''

export function parseOntologyFocus(params) {
  const chamberId = valueOf(params, 'chamber_id')
  const relationId = valueOf(params, 'relation_id')
  const graphRevision = valueOf(params, 'graph_revision')
  const supplied = [chamberId, relationId, graphRevision].filter(Boolean).length
  if (supplied === 0) return { phase: 'none' }
  if (supplied !== 3) {
    return {
      phase: 'invalid',
      message: '온톨로지 링크에 chamber_id·relation_id·graph_revision이 모두 필요합니다.',
    }
  }
  return { phase: 'ready', chamberId, relationId, graphRevision }
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
  const relation = normalized.relationships.find((item) => item.id === focus.relationId)
  if (!relation) return { ...focus, phase: 'not-found' }
  const source = normalized.nodes.find((item) => item.id === relation.source) ?? null
  const target = normalized.nodes.find((item) => item.id === relation.target) ?? null
  return { ...focus, phase: 'found', relation, source, target, graph: normalized }
}
