import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { CORE_CHAMBER_GRAPH } from '../src/shared/api/contractMocks.js'
import { DEFAULT_GRAPH_CHAMBER, GRAPH_CHAMBERS } from '../src/shared/graph/ontology-chambers.js'
import {
  PUBLIC_NODE_PROPERTIES,
  annotateWaferAlarmHints,
  attachWaferAlarmContext,
  buildOntologyOverviewLanes,
  buildLotContextGraph,
  connectedRelationIds,
  hasDisplayableRelationships,
  layoutOntologyNodes,
  lotOptionsForChamber,
  mergeOntologyGraphs,
  normalizeOntologyGraph,
  ontologyAlarmScope,
  orientOntologyRelationships,
  publicNodeDetails,
  summarizeOntologyAlarms,
} from '../src/shared/graph/ontology-graph.js'
import { parseOntologyFocus, resolveOntologyFocus } from '../src/features/knowledge/ontology-focus-state.js'

const FRONTEND_ROOT = fileURLToPath(new URL('..', import.meta.url))
const REPO_ROOT = resolve(FRONTEND_ROOT, '..')
const SOURCE_ROOT = resolve(FRONTEND_ROOT, 'src')

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const nested = await Promise.all(
    entries.map((entry) => {
      const path = resolve(directory, entry.name)
      if (entry.isDirectory()) return sourceFiles(path)
      return /\.(?:js|jsx)$/.test(entry.name) ? [path] : []
    }),
  )
  return nested.flat()
}

const masterGraph = await readFile(resolve(REPO_ROOT, 'infra/bootstrap/master_graph.cypher'), 'utf8')
const masterChambers = [...masterGraph.matchAll(/MERGE \(c:Chamber \{chamber_id:'([^']+)'\}\)/g)]
  .map((match) => match[1])
  .sort()
assert.deepEqual([...GRAPH_CHAMBERS].sort(), [...new Set(masterChambers)].sort(), 'selector는 master graph 12 chamber와 같아야 합니다')
assert.equal(DEFAULT_GRAPH_CHAMBER, 'EQP01-PM1')

const normalized = normalizeOntologyGraph(CORE_CHAMBER_GRAPH)
assert.equal(normalized.root_node_id, 'Chamber:EQP01-PM1')
assert.equal(normalized.nodes.length, CORE_CHAMBER_GRAPH.node_count)
assert.equal(normalized.relationships.length, CORE_CHAMBER_GRAPH.relationship_count)
assert.equal(hasDisplayableRelationships(normalized), true)
assert.equal(hasDisplayableRelationships({ nodes: normalized.nodes, relationships: [] }), false)
assert.equal(connectedRelationIds(normalized, 'Chamber:EQP01-PM1').size, 5)
assert.equal(connectedRelationIds(normalized, null).size, 0)

const positions = layoutOntologyNodes(normalized)
assert.equal(positions.length, normalized.nodes.length)
assert.equal(new Set(positions.map(({ position }) => `${position.x}:${position.y}`)).size, positions.length, '고정 layout node 좌표가 겹칩니다')
const presentationRelationships = orientOntologyRelationships(normalized, positions)
const measuredPresentation = presentationRelationships.find((relationship) => relationship.type === 'MEASURED_ON')
assert.equal(measuredPresentation.display_source, 'Chamber:EQP01-PM1')
assert.equal(measuredPresentation.display_target, 'Parameter:PH_DEV')
assert.equal(measuredPresentation.display_reversed, true)
const areaPresentation = presentationRelationships.find((relationship) => relationship.type === 'IN_AREA')
assert.equal(areaPresentation.display_source, 'ProcessStep:CT-PHOTO')
assert.equal(areaPresentation.display_target, 'Area:Photo')
assert.equal(areaPresentation.display_reversed, false)

const pm2Graph = {
  ...CORE_CHAMBER_GRAPH,
  context: { ...CORE_CHAMBER_GRAPH.context, chamber_id: 'EQP01-PM2' },
  relationships: CORE_CHAMBER_GRAPH.relationships.map((relationship) =>
    relationship.type === 'MEASURED_ON'
      ? { ...relationship, to_node_id: 'Chamber:EQP01-PM2' }
      : relationship,
  ),
}
const pm2Positions = layoutOntologyNodes(pm2Graph)
for (const chamberId of ['Chamber:EQP01-PM1', 'Chamber:EQP01-PM2']) {
  assert.deepEqual(
    pm2Positions.find(({ node }) => node.id === chamberId).position,
    positions.find(({ node }) => node.id === chamberId).position,
    `${chamberId} selector 변경으로 node 위치가 바뀌면 안 됩니다`,
  )
}
const pm2Measured = orientOntologyRelationships(pm2Graph, pm2Positions).find(
  (relationship) => relationship.type === 'MEASURED_ON',
)
assert.equal(pm2Measured.display_source, 'Chamber:EQP01-PM2', 'selector 변경은 Parameter 연결 Chamber를 바꿔야 합니다')

const etchGraph = {
  root_node_id: 'Chamber:EQP04-PM1',
  graph_revision: CORE_CHAMBER_GRAPH.graph_revision,
  nodes: [
    { node_id: 'EquipmentModel:ET-7500', label: 'EquipmentModel', business_id: 'ET-7500', name: 'Dry Etcher' },
    { node_id: 'ProcessStep:CT-ETCH', label: 'ProcessStep', business_id: 'CT-ETCH', name: 'CT-ETCH' },
    { node_id: 'Area:Etch', label: 'Area', business_id: 'Etch', name: 'Dry Etch' },
    {
      node_id: 'Equipment:EQP04',
      label: 'Equipment',
      business_id: 'EQP04',
      name: 'EQP04',
      properties: { equipment_id: 'EQP04', area: 'Etch', model_code: 'ET-7500' },
    },
    { node_id: 'Chamber:EQP04-PM1', label: 'Chamber', business_id: 'EQP04-PM1', name: 'EQP04-PM1' },
    { node_id: 'Parameter:ET_REFL', label: 'Parameter', business_id: 'ET_REFL', name: 'ET_REFL' },
  ],
  relationships: [
    { relation_id: 'ET-MODEL', type: 'OF_MODEL', from_node_id: 'Equipment:EQP04', to_node_id: 'EquipmentModel:ET-7500' },
    { relation_id: 'ET-STEP', type: 'PERFORMS', from_node_id: 'Equipment:EQP04', to_node_id: 'ProcessStep:CT-ETCH' },
    { relation_id: 'ET-AREA', type: 'IN_AREA', from_node_id: 'ProcessStep:CT-ETCH', to_node_id: 'Area:Etch' },
    { relation_id: 'ET-PART', type: 'PART_OF', from_node_id: 'Chamber:EQP04-PM1', to_node_id: 'Equipment:EQP04' },
    { relation_id: 'ET-MEASURED', type: 'MEASURED_ON', from_node_id: 'Parameter:ET_REFL', to_node_id: 'Chamber:EQP04-PM1' },
  ],
}
const mergedGraph = mergeOntologyGraphs([CORE_CHAMBER_GRAPH, etchGraph], 'Chamber:EQP01-PM1')
const mergedAgain = mergeOntologyGraphs([CORE_CHAMBER_GRAPH, CORE_CHAMBER_GRAPH])
assert.equal(mergedAgain.relationships.length, normalized.relationships.length, '반복 subgraph 관계는 중복되면 안 됩니다')
assert.equal(mergedGraph.nodes.filter((node) => node.label === 'EquipmentModel').length, 2)
assert.deepEqual(
  buildOntologyOverviewLanes([mergedGraph]).map((lane) => lane.model.business_id),
  ['PH-9000', 'ET-7500'],
  '전체 구조 안내는 공정 순서 Photo → Etch로 고정해야 합니다',
)
const mergedPositions = layoutOntologyNodes(mergedGraph)
assert.equal(mergedPositions.length, mergedGraph.nodes.length)
assert.equal(
  new Set(mergedPositions.map(({ position }) => `${position.x}:${position.y}`)).size,
  mergedPositions.length,
  '두 모델 통합 layout node 좌표가 겹칩니다',
)
assert.ok(
  mergedPositions.find(({ node }) => node.id === 'EquipmentModel:PH-9000').position.y <
    mergedPositions.find(({ node }) => node.id === 'EquipmentModel:ET-7500').position.y,
  'Photo와 Etch 모델은 서로 다른 lane에 배치해야 합니다',
)
const switchedRootGraph = { ...mergedGraph, root_node_id: 'Chamber:EQP04-PM1' }
for (const { node, position } of mergedPositions) {
  assert.deepEqual(
    layoutOntologyNodes(switchedRootGraph).find((item) => item.node.id === node.id).position,
    position,
    '조회 챔버 변경으로 통합 graph node 위치가 바뀌면 안 됩니다',
  )
}

assert.deepEqual(
  summarizeOntologyAlarms([
    { judgement: 'OOS', occurred_at: '2026-06-03T10:00:00+09:00' },
    { judgement: 'OOC', occurred_at: '2026-06-04T10:00:00+09:00' },
    { alarm_type: 'OOS', occurred_at: '2026-06-02T10:00:00+09:00' },
  ]),
  {
    total: 3,
    oos: 2,
    ooc: 1,
    actions: 0,
    pending_actions: 0,
    latest_occurred_at: '2026-06-04T10:00:00+09:00',
  },
)
assert.deepEqual(
  summarizeOntologyAlarms([
    { judgement: 'OOS', action_id: 'ACT-1', approval_status: 'PENDING' },
    { judgement: 'OOC', action_id: 'ACT-1', approval_status: 'PENDING' },
    { judgement: 'OOC', action_id: 'ACT-2', approval_status: 'APPROVED' },
  ]),
  { total: 3, oos: 1, ooc: 2, actions: 2, pending_actions: 1, latest_occurred_at: null },
)
assert.deepEqual(ontologyAlarmScope(normalized, normalized.nodes.find((node) => node.label === 'Parameter')), {
  requests: [{ sensor_id: 'PH_DEV' }],
  basis: '파라미터 PH_DEV',
})
assert.deepEqual(ontologyAlarmScope(normalized, normalized.nodes.find((node) => node.label === 'Chamber')), {
  requests: [{ chamber_id: 'EQP01-PM1' }],
  basis: '챔버 EQP01-PM1',
})
assert.deepEqual(ontologyAlarmScope(normalized, normalized.nodes.find((node) => node.label === 'Equipment')), {
  requests: [{ equipment_id: 'EQP01' }],
  basis: '설비 EQP01',
})
assert.deepEqual(ontologyAlarmScope(normalized, normalized.nodes.find((node) => node.label === 'Area')), {
  requests: [{ area: 'Photo' }],
  basis: '구역 Photo',
})
assert.deepEqual(ontologyAlarmScope(normalized, normalized.nodes.find((node) => node.label === 'EquipmentModel')), {
  requests: [{ equipment_id: 'EQP01' }],
  basis: '모델 연결 설비 1대',
})
assert.deepEqual(
  ontologyAlarmScope(normalized, normalized.nodes.find((node) => node.id === 'ProcessStep:CT-PHOTO')),
  { requests: [{ area: 'Photo' }], basis: '공정 구역 Photo' },
)
assert.equal(
  ontologyAlarmScope(normalized, normalized.nodes.find((node) => node.id === 'ProcessStep:CT-ETCH')),
  null,
)

const chamberWithPrivateProperty = normalizeOntologyGraph({
  root_node_id: 'Chamber:EQP01-PM1',
  graph_revision: 'revision-1',
  nodes: [
    {
      id: 'Chamber:EQP01-PM1',
      label: 'Chamber',
      business_id: 'EQP01-PM1',
      display_name: 'EQP01-PM1',
      properties: { chamber_id: 'EQP01-PM1', password: 'must-not-render', uri: 'must-not-render' },
    },
    {
      id: 'Equipment:EQP01',
      label: 'Equipment',
      business_id: 'EQP01',
      display_name: 'EQP01',
      properties: { equipment_id: 'EQP01' },
    },
  ],
  relationships: [{ id: 'REL-1', type: 'PART_OF', source: 'Chamber:EQP01-PM1', target: 'Equipment:EQP01' }],
})
assert.deepEqual(publicNodeDetails(chamberWithPrivateProperty.nodes[0]), [{ key: 'chamber_id', value: 'EQP01-PM1' }])
assert.ok(!Object.values(PUBLIC_NODE_PROPERTIES).flat().includes('password'))
assert.ok(!Object.values(PUBLIC_NODE_PROPERTIES).flat().includes('uri'))
assert.ok(!PUBLIC_NODE_PROPERTIES.Lot.includes('source_system'), 'LOT 화면에 데이터 출처를 노출하면 안 됩니다')

const lotContextGraph = {
  ...CORE_CHAMBER_GRAPH,
  nodes: [
    ...CORE_CHAMBER_GRAPH.nodes,
    { node_id: 'Lot:LOT010', label: 'Lot', business_id: 'LOT010', name: 'LOT010', properties: { lot_id: 'LOT010' } },
    { node_id: 'Wafer:LH-1', label: 'Wafer', business_id: 'WAFER-01', name: 'WAFER-01', properties: { lot_hist_id: 'LH-1', wafer_id: 'WAFER-01', lot_id: 'LOT010', step_id: 'CT-PHOTO', recipe_id: 'RECIPE03', track_in_at: '2026-08-18T09:00:00+09:00', track_out_at: '2026-08-18T09:01:00+09:00', chamber_wafer_cum: 12 } },
    { node_id: 'Wafer:LH-2', label: 'Wafer', business_id: 'WAFER-01', name: 'WAFER-01', properties: { lot_hist_id: 'LH-2', wafer_id: 'WAFER-01', lot_id: 'LOT010', step_id: 'CT-ETCH', recipe_id: 'RECIPE04', track_in_at: '2026-08-18T09:10:00+09:00', track_out_at: '2026-08-18T09:11:00+09:00', chamber_wafer_cum: 8 } },
  ],
  relationships: [
    ...CORE_CHAMBER_GRAPH.relationships,
    { relation_id: 'PG-C-1', type: 'CONTAINS', from_node_id: 'Lot:LOT010', to_node_id: 'Wafer:LH-1' },
    { relation_id: 'PG-C-2', type: 'CONTAINS', from_node_id: 'Lot:LOT010', to_node_id: 'Wafer:LH-2' },
    { relation_id: 'PG-P-1', type: 'PROCESSED_IN', from_node_id: 'Wafer:LH-1', to_node_id: 'Chamber:EQP01-PM1' },
    { relation_id: 'PG-P-2', type: 'PROCESSED_IN', from_node_id: 'Wafer:LH-2', to_node_id: 'Chamber:EQP01-PM2' },
  ],
}
assert.deepEqual(lotOptionsForChamber(lotContextGraph).map((lot) => lot.id), ['LOT010'])
assert.deepEqual(lotOptionsForChamber(lotContextGraph, 'EQP01-PM2').map((lot) => lot.id), ['LOT010'])
const lotSummary = buildLotContextGraph(lotContextGraph)
assert.equal(lotSummary.nodes.filter((node) => ['Lot', 'Wafer'].includes(node.label)).length, 0, '첫 진입에는 생산 이력 node를 표시하지 않아야 합니다')
const chamberScopedGraph = buildLotContextGraph(lotContextGraph, '', 'EQP01-PM1')
assert.ok(
  chamberScopedGraph.nodes.some((node) => node.id === 'Chamber:EQP01-PM2'),
  'Chamber만 선택한 구조 문맥에는 형제 Chamber를 포함해야 합니다',
)
assert.deepEqual(
  chamberScopedGraph.nodes.filter((node) => node.label === 'Lot').map((node) => node.business_id),
  ['LOT010'],
  'Chamber를 선택하면 해당 Chamber를 거친 LOT을 표시해야 합니다',
)
assert.equal(
  chamberScopedGraph.nodes.filter((node) => node.label === 'Wafer').length,
  0,
  'Chamber 선택 단계에서는 LOT만 요약하고 wafer를 펼치지 않아야 합니다',
)
assert.deepEqual(
  chamberScopedGraph.relationships.filter((relationship) => relationship.type === 'PROCESSED_IN'),
  [{ id: 'VIEW-LOT-PROCESSED-IN-Lot:LOT010-Chamber:EQP01-PM1', type: 'PROCESSED_IN', source: 'Lot:LOT010', target: 'Chamber:EQP01-PM1' }],
  'LOT 요약은 선택 Chamber와 처리 관계를 유지해야 합니다',
)
const chamberScopedPositions = layoutOntologyNodes(chamberScopedGraph)
assert.deepEqual(
  chamberScopedPositions.find(({ node }) => node.label === 'Lot').position,
  { x: 920, y: 620 },
  'Chamber LOT 요약은 선택 Chamber 아래 그래프 하단 한 줄에서 시작해야 합니다',
)
const selectedLotGraph = buildLotContextGraph(lotContextGraph, 'LOT010')
assert.equal(selectedLotGraph.nodes.filter((node) => node.label === 'Wafer').length, 0, 'LOT 단독 조회는 공정 경로만 보여 주고 Wafer는 펼치지 않아야 합니다')
assert.equal(selectedLotGraph.relationships.filter((relationship) => relationship.type === 'PROCESSED_IN').length, 2)
const selectedLotNode = selectedLotGraph.nodes.find((node) => node.label === 'Lot')
assert.deepEqual(selectedLotNode.properties.alarm_history_refs, [
  { chamber_id: 'EQP01-PM1', lot_hist_id: 'LH-1' },
  { chamber_id: 'EQP01-PM2', lot_hist_id: 'LH-2' },
], 'LOT 단독 선택은 전체 처리 이력을 알람 조회 범위로 보존해야 합니다')
assert.deepEqual(ontologyAlarmScope(selectedLotGraph, selectedLotNode), {
  requests: [{ chamber_id: 'EQP01-PM1' }, { chamber_id: 'EQP01-PM2' }],
  lot_id: 'LOT010',
  lot_hist_ids: ['LH-1', 'LH-2'],
  lot: true,
  basis: 'LOT LOT010 · 처리 이력 2건',
}, 'LOT 단독 조회는 임의 root Chamber가 아닌 모든 처리 이력의 알람을 집계해야 합니다')
const selectedWaferGraph = buildLotContextGraph(lotContextGraph, 'LOT010', '', 'Wafer:LH-1')
assert.equal(selectedWaferGraph.nodes.filter((node) => node.label === 'Wafer').length, 1, '선택한 Wafer만 공정 경로에 표시해야 합니다')
const selectedWaferNode = selectedWaferGraph.nodes.find((node) => node.id === 'Wafer:LH-1')
assert.deepEqual(selectedWaferNode.properties.alarm_history_refs, [
  { chamber_id: 'EQP01-PM1', lot_hist_id: 'LH-1' },
  { chamber_id: 'EQP01-PM2', lot_hist_id: 'LH-2' },
], '동일 물리 Wafer의 모든 공정 이력을 알람 조회 범위로 보존해야 합니다')
assert.deepEqual(selectedWaferNode.properties.process_history, [
  { chamber_id: 'EQP01-PM1', lot_hist_id: 'LH-1', step_id: 'CT-PHOTO', recipe_id: 'RECIPE03', track_in_at: '2026-08-18T09:00:00+09:00', track_out_at: '2026-08-18T09:01:00+09:00', chamber_wafer_cum: 12 },
  { chamber_id: 'EQP01-PM2', lot_hist_id: 'LH-2', step_id: 'CT-ETCH', recipe_id: 'RECIPE04', track_in_at: '2026-08-18T09:10:00+09:00', track_out_at: '2026-08-18T09:11:00+09:00', chamber_wafer_cum: 8 },
], '동일 물리 Wafer의 Photo·Etch 처리 이력을 우측 패널용으로 모두 보존해야 합니다')
const reworkLotContextGraph = {
  ...lotContextGraph,
  nodes: [...lotContextGraph.nodes, {
    node_id: 'Wafer:LH-3', label: 'Wafer', business_id: 'WAFER-01', name: 'WAFER-01',
    properties: { lot_hist_id: 'LH-3', wafer_id: 'WAFER-01', lot_id: 'LOT010', step_id: 'CT-PHOTO', recipe_id: 'RECIPE03-RW', track_in_at: '2026-08-18T09:20:00+09:00', track_out_at: '2026-08-18T09:21:00+09:00', chamber_wafer_cum: 13 },
  }],
  relationships: [...lotContextGraph.relationships,
    { relation_id: 'CONTAINS-LOT010-LH3', type: 'CONTAINS', source: 'Lot:LOT010', target: 'Wafer:LH-3' },
    { relation_id: 'PROCESSED-IN-LH3-PM1', type: 'PROCESSED_IN', source: 'Wafer:LH-3', target: 'Chamber:EQP01-PM1' },
  ],
}
const reworkWaferGraph = buildLotContextGraph(reworkLotContextGraph, 'LOT010', '', 'Wafer:LH-1')
assert.deepEqual(
  reworkWaferGraph.nodes.find((node) => node.id === 'Wafer:LH-1').properties.process_history.map((history) => history.lot_hist_id),
  ['LH-1', 'LH-2', 'LH-3'],
  '동일 Wafer가 같은 Chamber를 재방문해도 모든 처리 이력을 보존해야 합니다',
)
assert.deepEqual(ontologyAlarmScope(selectedWaferGraph, selectedWaferNode), {
  requests: [{ chamber_id: 'EQP01-PM1' }, { chamber_id: 'EQP01-PM2' }],
  lot_id: 'LOT010',
  lot_hist_ids: ['LH-1', 'LH-2'],
  wafer: true,
  basis: 'Wafer WAFER-01 · EQP01-PM1, EQP01-PM2',
}, '동일 Wafer의 Photo·Etch 알람을 함께 조회해야 합니다')
const incidentGraph = buildLotContextGraph(lotContextGraph, 'LOT010', 'EQP01-PM2')
assert.equal(incidentGraph.nodes.filter((node) => node.label === 'Wafer').length, 1)
assert.deepEqual(
  incidentGraph.nodes.filter((node) => node.label === 'Chamber').map((node) => node.business_id),
  ['EQP01-PM2'],
  'LOT까지 선택한 incident graph에는 선택 Chamber만 남겨야 합니다',
)
assert.ok(
  incidentGraph.nodes.some((node) => node.label === 'Area') &&
    incidentGraph.nodes.some((node) => node.label === 'ProcessStep') &&
    incidentGraph.nodes.some((node) => node.label === 'EquipmentModel'),
  'LOT incident graph에도 선택 Chamber의 상위 구조는 남겨야 합니다',
)
assert.ok(
  !incidentGraph.relationships.some((relationship) => relationship.type === 'NEXT_STEP'),
  'LOT incident graph에는 인접 공정 관계를 표시하지 않아야 합니다',
)
assert.deepEqual(
  incidentGraph.relationships.filter((relationship) => relationship.type === 'PROCESSED_IN').map((relationship) => relationship.target),
  ['Chamber:EQP01-PM2'],
  'Lot과 Chamber를 함께 고르면 해당 incident의 wafer-chamber 관계만 남겨야 합니다',
)
assert.equal(incidentGraph.relationships.find((relationship) => relationship.type === 'PROCESSED_IN').source, 'Lot:LOT010')
const incidentPositions = layoutOntologyNodes(incidentGraph)
assert.equal(
  incidentPositions.find(({ node }) => node.id === 'Chamber:EQP01-PM2').position.y,
  incidentPositions.find(({ node }) => node.label === 'Equipment').position.y,
  'LOT incident에서 선택 Chamber는 Equipment와 같은 축에 배치해야 합니다',
)
assert.ok(
  (await readFile(resolve(SOURCE_ROOT, 'shared/graph/ontology-graph.js'), 'utf8')).includes('columnsPerRow: 3'),
  'LOT incident wafer는 한 줄에 세 개씩 배치해야 합니다',
)
assert.ok(
  (await readFile(resolve(SOURCE_ROOT, 'shared/graph/ontology-graph.js'), 'utf8')).includes('? 108 : waferRowGap'),
  'ALARM badge가 있는 wafer rack은 행 간격을 넓혀야 합니다',
)
assert.deepEqual(
  incidentPositions.find(({ node }) => node.label === 'Lot').position,
  { x: 905, y: 230 },
  'LOT은 선택 Chamber 아래의 처리 이력 anchor에 배치해야 합니다',
)
assert.deepEqual(
  incidentPositions.find(({ node }) => node.label === 'Wafer').position,
  { x: 929, y: 400 },
  'Wafer는 LOT 아래 rack의 첫 칸에서 시작해야 합니다',
)
const incidentPresentation = orientOntologyRelationships(incidentGraph, incidentPositions)
const lotHistoryPresentation = incidentPresentation.find((relationship) => relationship.type === 'PROCESSED_IN')
assert.equal(lotHistoryPresentation.display_source, 'Chamber:EQP01-PM2')
assert.equal(lotHistoryPresentation.display_target, 'Lot:LOT010')
assert.equal(lotHistoryPresentation.display_reversed, true)
const containmentPresentation = incidentPresentation.find((relationship) => relationship.type === 'CONTAINS')
assert.equal(containmentPresentation.display_source, 'Lot:LOT010')
assert.equal(containmentPresentation.display_target, 'Wafer:LH-2')
assert.equal(containmentPresentation.display_vertical, true)
const waferAlarmGraph = attachWaferAlarmContext(selectedWaferGraph, 'Wafer:LH-1', [
  { source: 'TRACE', alarm_id: 'ALM-1', judgement: 'OOS', rule_id: 'USL', sensor_id: 'PH_FOCUS', occurred_at: '2026-08-18T09:00:00+09:00' },
])
assert.ok(!waferAlarmGraph.nodes.some((node) => node.label === 'Alarm'))
assert.ok(waferAlarmGraph.relationships.some((relationship) => relationship.type === 'ALARM_ON' && relationship.target === 'Parameter:PH_FOCUS'))
const hintedWaferGraph = annotateWaferAlarmHints(selectedWaferGraph, [
  { lot_hist_id: 'LH-1', alarm_id: 'ALM-1' },
  { lot_hist_id: 'LH-1', alarm_id: 'ALM-2' },
])
assert.equal(hintedWaferGraph.nodes.find((node) => node.id === 'Wafer:LH-1').properties.alarm_count, 2)

const noFocus = parseOntologyFocus(new URLSearchParams())
assert.equal(noFocus.phase, 'none')
assert.equal(parseOntologyFocus(new URLSearchParams('chamber_id=EQP01-PM1')).phase, 'invalid')
const focus = parseOntologyFocus(
  new URLSearchParams(
    `chamber_id=EQP01-PM1&relation_id=${CORE_CHAMBER_GRAPH.relationships[0].relation_id}&graph_revision=${CORE_CHAMBER_GRAPH.graph_revision}`,
  ),
)
assert.equal(resolveOntologyFocus(CORE_CHAMBER_GRAPH, focus).phase, 'found')
assert.equal(resolveOntologyFocus(CORE_CHAMBER_GRAPH, { ...focus, graphRevision: 'stale' }).phase, 'revision-mismatch')
const impactFocus = parseOntologyFocus(new URLSearchParams(
  `chamber_id=EQP01-PM1&graph_revision=${CORE_CHAMBER_GRAPH.graph_revision}&direct_node_ids=Chamber%3AEQP01-PM1,Parameter%3APH_FOCUS&check_node_ids=ProcessStep%3ACT-ETCH,Chamber%3AEQP01-PM2`,
))
assert.equal(impactFocus.kind, 'impact')
const resolvedImpact = resolveOntologyFocus(CORE_CHAMBER_GRAPH, impactFocus)
assert.equal(resolvedImpact.phase, 'found')
assert.deepEqual(resolvedImpact.directNodes.map((node) => node.id), ['Chamber:EQP01-PM1', 'Parameter:PH_FOCUS'])
assert.deepEqual(resolvedImpact.checkNodes.map((node) => node.id), ['ProcessStep:CT-ETCH', 'Chamber:EQP01-PM2'])

const scopedDirectories = [
  resolve(SOURCE_ROOT, 'features/knowledge'),
  resolve(SOURCE_ROOT, 'features/agent'),
  resolve(SOURCE_ROOT, 'shared/graph'),
  resolve(SOURCE_ROOT, 'shared/components/ontology'),
  resolve(SOURCE_ROOT, 'shared/trace'),
  resolve(SOURCE_ROOT, 'shared/components/trace'),
]
const scopedFiles = (await Promise.all(scopedDirectories.map(sourceFiles))).flat().filter((path) => !/[\\/]mock[\\/]/.test(path))
const scopedSource = (await Promise.all(scopedFiles.map((path) => readFile(path, 'utf8')))).join('\n')
assert.doesNotMatch(scopedSource, /PHO-|ETC-|AREA_BY_PREFIX/, 'B-4.2 소유 경로에 구 ID·AREA 접두 추측을 둘 수 없습니다')
assert.doesNotMatch(scopedSource, /<iframe|bolt:\/\/|neo4j:\/\/|MATCH \(/, '브라우저 iframe·Neo4j URI·Cypher를 노출할 수 없습니다')

const featureFiles = (
  await Promise.all(['agent', 'knowledge', 'detection'].map((name) => sourceFiles(resolve(SOURCE_ROOT, 'features', name))))
).flat()
for (const file of featureFiles) {
  const owner = file.match(/\/features\/([^/]+)\//)?.[1]
  const source = await readFile(file, 'utf8')
  for (const match of source.matchAll(/from\s+['"](\.[^'"]+)['"]/g)) {
    const target = resolve(dirname(file), match[1])
    const targetOwner = target.match(/\/features\/([^/]+)\//)?.[1]
    assert.ok(!targetOwner || targetOwner === owner, `${file}에서 ${targetOwner} feature를 직접 import합니다`)
  }
}

const canvasSource = await readFile(resolve(SOURCE_ROOT, 'shared/components/ontology/OntologyGraphCanvas.jsx'), 'utf8')
assert.ok(canvasSource.includes('ALARM {alarmCount}'))
assert.ok(canvasSource.includes('function LotHistoryEdge'), 'Chamber-LOT 처리 이력은 별도 버스 edge로 그려야 합니다')
assert.ok(canvasSource.includes("sourceHandle: isLotHistory ? 'left-source'"), 'LOT 처리 이력은 Chamber 왼쪽 여백으로 빠져야 합니다')
assert.ok(canvasSource.includes('const busX = sourceX - 150'), 'LOT 처리 이력은 구조 관계와 겹치지 않는 세로 버스를 사용해야 합니다')
assert.ok(!canvasSource.includes("relationship.type !== 'CONTAINS'"), 'LOT-Wafer CONTAINS 관계를 canvas에 표시해야 합니다')
assert.ok(canvasSource.includes('조회 기준') && canvasSource.includes('선택 LOT') && canvasSource.includes('상세 확인'), '그래프 기준·LOT·현재 선택 상태를 업무 용어로 구분해야 합니다')
for (const contract of [
  'nodesDraggable={false}',
  'nodesConnectable={false}',
  'edgesReconnectable={false}',
  'connectOnClick={false}',
  'fitView',
  'nodes: initialEvidenceNodes',
  'useReactFlow',
  'duration: 320',
  'sourcePosition: Position.Right',
  'targetPosition: Position.Left',
  'zoomOnScroll',
  '<Controls position="top-right"',
  'onPaneClick',
  'selectedNodeId',
  'impactNodeIds',
  'checkRequiredNodeIds',
  'root && !selectedNodeId',
  "viewport === 'page'",
  "viewport === 'modal'",
  "'h-[400px] min-h-[340px]'",
  'padding={modalViewport ? 0.08 : 0.65}',
  'maxZoom={modalViewport ? 1.25 : 1.05}',
  'graphKey={layoutKey}',
]) {
  assert.ok(canvasSource.includes(contract), `xyflow read-only 계약 누락: ${contract}`)
}

const ontologyPageSource = await readFile(resolve(SOURCE_ROOT, 'features/knowledge/pages/OntologyPage.jsx'), 'utf8')
assert.ok(ontologyPageSource.includes('Promise.allSettled(orderedChambers.map'))
assert.ok(ontologyPageSource.includes("requestedResult.status !== 'fulfilled'"))
assert.ok(ontologyPageSource.includes('mergeOntologyGraphs(responses'))
assert.ok(ontologyPageSource.includes('생산 이력 반환 상한에 도달해'))
assert.ok(ontologyPageSource.includes('전체 구조 안내'))
assert.ok(ontologyPageSource.includes("const [isOverviewOpen, setIsOverviewOpen] = useState(true)"), '전체 구조 안내는 기본으로 열려 있어야 합니다')
assert.ok(ontologyPageSource.includes('aria-expanded={isOpen}'), '박스 내부 토글의 상태를 접근 가능하게 제공해야 합니다')
assert.ok(ontologyPageSource.includes("{isOpen ? '△' : '▽'}"), '전체 구조 안내 박스 우측 상단에 접기/펼치기 아이콘이 있어야 합니다')
assert.ok(ontologyPageSource.includes('isOpen={isOverviewOpen} onToggle={() => setIsOverviewOpen((open) => !open)}'), '박스 내부 토글로 전체 구조 안내 내용을 열고 닫아야 합니다')
assert.ok(ontologyPageSource.includes('{isOpen && <div className="mt-3 overflow-x-auto pb-1">'), '토글을 닫으면 전체 구조 안내의 큰 내용만 숨겨야 합니다')
assert.ok(ontologyPageSource.includes("activeBrowseMode === 'structure' && !explicitChamber && !activeLotId"), '전체 구조 탭에서만 전체 구조 안내를 표시해야 합니다')
assert.ok(ontologyPageSource.includes('buildOntologyOverviewLanes([graph])'))
assert.ok(ontologyPageSource.includes('onSelectNode={selectOverviewNode}'))
assert.ok(ontologyPageSource.includes('전체 구조 안내에서는 node 종류와 무관하게 상세 조회 대상만 바꾼다.'), 'overview button은 selector를 바꾸지 않아야 합니다')
assert.ok(ontologyPageSource.includes("focus.phase === 'ready' ? focus.chamberId : ''"), '일반 진입은 선택·포커스 없는 전체 구조여야 합니다')
assert.ok(ontologyPageSource.includes('md:grid-cols-[minmax(0,1fr)_280px]'), '중간 폭 데스크톱에서도 우측 패널을 그래프 옆에 배치해야 합니다')
assert.ok(ontologyPageSource.includes('lg:grid-cols-[minmax(0,1fr)_320px]'), '화면 폭에 따라 우측 패널 폭을 단계적으로 넓혀야 합니다')
assert.ok(ontologyPageSource.includes('md:sticky md:top-4'), '중간 폭 이상에서 우측 패널이 화면을 따라가야 합니다')
assert.ok(ontologyPageSource.includes('const graphScopeLabel'), '그래프 요약은 현재 LOT/Wafer 문맥을 표시해야 합니다')
assert.ok(ontologyPageSource.includes('<GraphSummary graph={graph} compact scopeLabel={graphScopeLabel} />'))
assert.ok(ontologyPageSource.includes('const graphViewKey'), 'LOT/Wafer selector가 graph projection을 바꾸면 Canvas를 다시 초기화해야 합니다')
assert.ok(ontologyPageSource.includes('key={graphViewKey} graph={graph}'), 'LOT/Wafer 선택 시 이전 전체 구조 viewport를 재사용하지 않아야 합니다')
assert.ok(ontologyPageSource.includes("const activeLotId = selectedLot || (activeBrowseMode === 'lot' ? lotOptions[0]?.id ?? '' : '')"), 'LOT / WAFER 탭 진입은 최상단 LOT를 유효 선택값으로 사용해야 합니다')
assert.ok(ontologyPageSource.includes('<option value="">전체 구조</option>'))
assert.ok(ontologyPageSource.includes('viewport="page"'), '독립 Ontology 화면만 확장 viewport를 사용해야 합니다')
assert.ok(ontologyPageSource.includes("emphasizeRoot={focus.phase === 'ready'}"), '일반 Chamber selector는 root를 강조하면 안 됩니다')
assert.ok(ontologyPageSource.includes("setSearchParams({}, { replace: true })"), 'selector 변경은 focus query를 모두 제거해야 합니다')
assert.ok(ontologyPageSource.includes("status: hasDisplayableRelationships(merged) ? 'success' : 'empty'"))
assert.ok(ontologyPageSource.includes('현재 선택'), '선택한 node의 상태 badge가 필요합니다')
assert.ok(ontologyPageSource.includes('표시 관계 {relationCount}건'), '선택한 node의 표시 관계 상태가 필요합니다')
assert.ok(ontologyPageSource.includes('Promise.allSettled(scope.requests.map'))
assert.ok(ontologyPageSource.includes('선택 노드 운영 요약'))
assert.ok(ontologyPageSource.includes("node.label === 'Wafer'"), 'Wafer에는 Incident 전체 알람을 귀속하면 안 됩니다')
assert.ok(ontologyPageSource.includes('Wafer 알람 · 관련 Parameter'))
assert.ok(ontologyPageSource.includes('const alarmGroups'), 'Wafer 알람은 처리 이력별로 그룹화해야 합니다')
assert.ok(ontologyPageSource.includes('PROCESS HISTORY') && ontologyPageSource.includes('CHAMBER SEQUENCE'), 'Wafer는 내부 ID 대신 공정 처리 이력을 표시해야 합니다')
assert.ok(ontologyPageSource.includes('선택 Incident 알람 요약'))
assert.ok(ontologyPageSource.includes('선택 LOT 알람 요약'), 'LOT 단독 선택은 Incident가 아닌 전체 LOT 알람 요약으로 표시해야 합니다')
assert.ok(ontologyPageSource.includes('처리 이력 {index + 1}'), 'Wafer 우측 패널은 공정 처리 이력을 각각 표시해야 합니다')
assert.ok(ontologyPageSource.includes('alarm.lot_id !== scope.lot_id'))
assert.ok(ontologyPageSource.includes('alarm.lot_hist_id !== scope.lot_hist_id'))
assert.ok(ontologyPageSource.includes("scope.lot_hist_ids.includes(alarm.lot_hist_id)"), 'Wafer의 모든 공정 이력을 알람 범위로 사용해야 합니다')
assert.ok(ontologyPageSource.includes("node.label === 'Lot' && node.business_id === activeLotId"), 'LOT 선택 시 Lot 정보를 우측 패널 기본값으로 써야 합니다')
assert.ok(ontologyPageSource.includes('const visualSelectedNode'), 'LOT 조회 context와 node 선택 상태를 분리해야 합니다')
assert.ok(ontologyPageSource.includes('const panelNode'), 'LOT 선택 시 우측 패널 기본 node가 필요합니다')
assert.ok(ontologyPageSource.includes('const selectedChamberNode'), 'Chamber selector의 우측 패널 기본 node가 필요합니다')
assert.ok(ontologyPageSource.includes('selectedNode ?? selectedWaferNode ?? selectedLotNode ?? selectedChamberNode'), '그래프 선택 없이 Wafer/Lot/Chamber panel 기본값을 정해야 합니다')
assert.ok(ontologyPageSource.includes('const waferAlarmNode'), 'Wafer selector와 그래프 클릭 모두 ALARM_ON 대상 node를 정해야 합니다')
assert.ok(ontologyPageSource.includes('const waferAlarmNode = selectedWaferNode'), 'Wafer selector 선택은 그래프 클릭 없이도 ALARM_ON 관계를 표시해야 합니다')
assert.ok(ontologyPageSource.includes('const activeAlarmState'), '이전 선택의 알람을 새 Wafer에 잠시 연결하면 안 됩니다')
assert.ok(ontologyPageSource.includes('scopeNodeId={selectedChamberNode?.id ?? null}'), '선택 Chamber를 graph scope로 전달해야 합니다')
assert.ok(ontologyPageSource.includes('incidentNodeId={selectedWafer ? null : selectedLotNode?.id ?? null}'), 'Wafer 선택 시에는 LOT incident 강조를 해제해야 합니다')
assert.ok(ontologyPageSource.includes("['ACTION', summary.actions"))
assert.ok(ontologyPageSource.includes("timeZone: 'Asia/Seoul'"))
assert.ok(ontologyPageSource.includes("hourCycle: 'h23'"), '시각은 오전/오후 없이 24시간 형식으로 표시해야 합니다')
assert.ok(ontologyPageSource.includes('MOCK 대응 데이터 없음'))
assert.ok(ontologyPageSource.includes('Agent 영향 범위'))
assert.ok(!ontologyPageSource.includes('직접 · {node.business_id}'))
assert.ok(ontologyPageSource.includes('확인 필요 · {node.business_id}'))
assert.ok(ontologyPageSource.includes('impactNodeIds={focusedImpactNodeIds}'))
assert.ok(ontologyPageSource.includes('실 API 결과가 아닙니다'))
assert.ok(ontologyPageSource.includes('state.partial'))
assert.doesNotMatch(ontologyPageSource, /error\.message/, 'API 원문 오류를 화면에 노출하면 안 됩니다')
assert.doesNotMatch(ontologyPageSource, /알람에서 공정 이력으로 추적|selectAlarmRoute|alarmBrowse/, '알람 목록을 시작점으로 LOT·WAFER 이력을 역추적하지 않습니다')

const detectionSource = await readFile(resolve(SOURCE_ROOT, 'shared/api/detection.js'), 'utf8')
assert.match(detectionSource, /ALL_ALARMS_MAX_PAGES\s*=\s*20/)
assert.ok(detectionSource.includes('page >= ALL_ALARMS_MAX_PAGES'))
assert.ok(detectionSource.includes('partial: items.length < total'))

const redirectSource = await readFile(resolve(SOURCE_ROOT, 'app/KnowledgeRedirect.jsx'), 'utf8')
assert.match(redirectSource, /pathname:\s*['"]\/ontology['"]/)
assert.match(redirectSource, /search/)

const summarySource = await readFile(resolve(SOURCE_ROOT, 'features/agent/components/RunSummaryCard.jsx'), 'utf8')
assert.match(summarySource, /measuredText\(repAlarm\?\.area\)/)
assert.doesNotMatch(summarySource, /AREA_BY_PREFIX|PHO-|ETC-/)
for (const deleted of ['RunContextBar.jsx', 'RunEvidencePanel.jsx', 'RunToolCallsCard.jsx']) {
  assert.ok(!featureFiles.some((path) => path.endsWith(`/agent/components/${deleted}`)), `${deleted}는 삭제 상태여야 합니다`)
}

const packageJson = JSON.parse(await readFile(resolve(FRONTEND_ROOT, 'package.json'), 'utf8'))
assert.equal(packageJson.dependencies['@xyflow/react'], '12.11.5')

console.log('ontology-graph: 12 chamber · shared xyflow · allowlist · no iframe · feature boundary 통과')
