import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { CORE_CHAMBER_GRAPH } from '../src/shared/api/contractMocks.js'
import { DEFAULT_GRAPH_CHAMBER, GRAPH_CHAMBERS } from '../src/shared/graph/ontology-chambers.js'
import {
  PUBLIC_NODE_PROPERTIES,
  buildOntologyOverviewLanes,
  connectedRelationIds,
  hasDisplayableRelationships,
  layoutOntologyNodes,
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

const scopedDirectories = [
  resolve(SOURCE_ROOT, 'features/knowledge'),
  resolve(SOURCE_ROOT, 'features/agent'),
  resolve(SOURCE_ROOT, 'shared/graph'),
  resolve(SOURCE_ROOT, 'shared/components/ontology'),
  resolve(SOURCE_ROOT, 'shared/trace'),
  resolve(SOURCE_ROOT, 'shared/components/trace'),
]
const scopedFiles = (await Promise.all(scopedDirectories.map(sourceFiles))).flat().filter((path) => !path.includes('/mock/'))
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
  'root && !selectedNodeId',
  "viewport === 'page'",
]) {
  assert.ok(canvasSource.includes(contract), `xyflow read-only 계약 누락: ${contract}`)
}

const ontologyPageSource = await readFile(resolve(SOURCE_ROOT, 'features/knowledge/pages/OntologyPage.jsx'), 'utf8')
assert.ok(ontologyPageSource.includes('Promise.all(orderedChambers.map'))
assert.ok(ontologyPageSource.includes('mergeOntologyGraphs(responses'))
assert.ok(ontologyPageSource.includes('전체 구조 안내'))
assert.ok(ontologyPageSource.includes('buildOntologyOverviewLanes([graph])'))
assert.ok(ontologyPageSource.includes('onSelectChamber={changeChamber}'))
assert.ok(ontologyPageSource.includes('xl:grid-cols-[minmax(0,1fr)_360px]'))
assert.ok(ontologyPageSource.includes('<GraphSummary graph={graph} compact />'))
assert.ok(ontologyPageSource.includes('viewport="page"'), '독립 Ontology 화면만 확장 viewport를 사용해야 합니다')
assert.ok(ontologyPageSource.includes("setSearchParams({}, { replace: true })"), 'selector 변경은 focus query를 모두 제거해야 합니다')
assert.ok(ontologyPageSource.includes("status: hasDisplayableRelationships(merged) ? 'success' : 'empty'"))
assert.ok(ontologyPageSource.includes('현재 선택'), '선택한 node의 상태 badge가 필요합니다')
assert.ok(ontologyPageSource.includes('직접 관계 {relationCount}건'), '선택한 node의 직접 관계 상태가 필요합니다')
assert.ok(ontologyPageSource.includes('Promise.all(scope.requests.map'))
assert.ok(ontologyPageSource.includes('선택 노드 운영 요약'))
assert.ok(ontologyPageSource.includes("['ACTION', summary.actions"))
assert.ok(ontologyPageSource.includes("timeZone: 'Asia/Seoul'"))
assert.ok(ontologyPageSource.includes('MOCK 대응 데이터 없음'))
assert.ok(ontologyPageSource.includes('실 API 결과가 아닙니다'))
assert.ok(ontologyPageSource.includes('state.partial'))
assert.doesNotMatch(ontologyPageSource, /error\.message/, 'API 원문 오류를 화면에 노출하면 안 됩니다')

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
