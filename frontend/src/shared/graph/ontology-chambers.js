// infra/bootstrap/master_graph.cypher의 Chamber business key 정본과 exact match해야 한다.
export const GRAPH_CHAMBERS = Object.freeze([
  'EQP01-PM1',
  'EQP01-PM2',
  'EQP02-PM1',
  'EQP02-PM2',
  'EQP03-PM1',
  'EQP03-PM2',
  'EQP04-PM1',
  'EQP04-PM2',
  'EQP05-PM1',
  'EQP05-PM2',
  'EQP06-PM1',
  'EQP06-PM2',
])

export const DEFAULT_GRAPH_CHAMBER = GRAPH_CHAMBERS[0]
