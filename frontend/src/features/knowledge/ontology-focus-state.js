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

const normalizedNode = (node) => ({
  ...node,
  id: node.node_id ?? node.id,
  display_name: node.name ?? node.display_name ?? node.business_id,
})

const normalizedRelation = (relation) => ({
  ...relation,
  id: relation.relation_id ?? relation.id,
  source: relation.from_node_id ?? relation.source,
  target: relation.to_node_id ?? relation.target,
})

export function normalizeOntologyGraph(graph) {
  if (!graph) return null
  const nodes = (graph.nodes ?? []).map(normalizedNode)
  const relationships = (graph.relationships ?? []).map(normalizedRelation)
  const chamberId = graph.context?.chamber_id
  return {
    ...graph,
    root_node_id: graph.root_node_id ?? (chamberId ? `Chamber:${chamberId}` : nodes[0]?.id),
    nodes,
    relationships,
  }
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
