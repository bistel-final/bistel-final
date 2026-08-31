import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'

process.env.VITE_USE_MOCK = 'false'

const fixtureUrl = new URL('../../backend/tests/fixtures/v5_cm_4_4/api_contract_baseline.json', import.meta.url)
const fixture = JSON.parse(await readFile(fixtureUrl, 'utf8'))
const components = fixture.components
const agentSource = await readFile(new URL('../src/shared/api/agent.js', import.meta.url), 'utf8')
assert.doesNotMatch(
  agentSource,
  /apiClient\.(?:get|post)\(['"]\/(?:agent\/runs|approvals)\/paged/,
  '미구현 Agent paged endpoint를 호출하면 안 됩니다',
)
const aliasRegistryUrl = new URL('../../docs/deliverables/api/compatibility_alias_registry.json', import.meta.url)
const aliasRegistry = JSON.parse(await readFile(aliasRegistryUrl, 'utf8'))

const appRoot = new URL('../src/app/', import.meta.url)
for (const entry of await readdir(appRoot, { withFileTypes: true })) {
  if (!entry.isFile()) continue
  const source = await readFile(new URL(entry.name, appRoot), 'utf8')
  assert.doesNotMatch(source, /getAuditLogs|shared\/api\/(?:analytics|audit)/, `Common shell must not call audit API: ${entry.name}`)
}

const coreOperations = fixture.operations.filter(
  (operation) => !operation.path.startsWith('/internal/') && !operation.path.startsWith('/health'),
)
assert.equal(coreOperations.length, 11, 'core public operation count drifted')

const validateScalar = (schema, value, label) => {
  if (value === null) {
    if (schema.type === 'null') return
    assert.equal(schema.nullable, true, `${label} must not be null`)
    return
  }
  if (schema.ref) return validateComponent(schema.ref, value, label)
  if (schema.type === 'string') {
    assert.equal(typeof value, 'string', `${label} must be a string`)
    if (schema.min_length != null) assert.ok(value.length >= schema.min_length, `${label} is too short`)
    if (schema.max_length != null) assert.ok(value.length <= schema.max_length, `${label} is too long`)
    if (schema.pattern) assert.match(value, new RegExp(schema.pattern), `${label} pattern mismatch`)
    if (schema.format === 'date-time') {
      assert.match(value, /(Z|[+-]\d\d:\d\d)$/, `${label} must include a UTC offset`)
      assert.ok(!Number.isNaN(Date.parse(value)), `${label} is not a date-time`)
    }
  } else if (schema.type === 'integer') {
    assert.ok(Number.isInteger(value), `${label} must be an integer`)
  } else if (schema.type === 'number') {
    assert.equal(typeof value, 'number', `${label} must be a number`)
  } else if (schema.type === 'boolean') {
    assert.equal(typeof value, 'boolean', `${label} must be a boolean`)
  } else if (schema.type === 'array') {
    assert.ok(Array.isArray(value), `${label} must be an array`)
    value.forEach((item, index) => validateScalar(schema.items, item, `${label}[${index}]`))
  } else if (schema.type === 'object') {
    assert.ok(value && typeof value === 'object' && !Array.isArray(value), `${label} must be an object`)
  }
  if (schema.enum) assert.ok(schema.enum.includes(value), `${label} enum mismatch`)
  if (schema.minimum != null) assert.ok(value >= schema.minimum, `${label} is below minimum`)
  if (schema.maximum != null) assert.ok(value <= schema.maximum, `${label} is above maximum`)
}

function validateComponent(componentName, value, label = componentName) {
  const component = components[componentName]
  assert.ok(component, `unknown component ${componentName}`)
  if (component.type === 'discriminated_union') {
    const variant = component.variants[value?.[component.discriminator]]
    assert.ok(variant, `${label} has an unknown discriminator`)
    return validateComponent(variant, value, label)
  }
  assert.ok(value && typeof value === 'object' && !Array.isArray(value), `${label} must be an object`)
  const fields = component.fields
  const required = Object.entries(fields)
    .filter(([, schema]) => schema.required)
    .map(([field]) => field)
  for (const field of required) {
    assert.ok(Object.hasOwn(value, field), `${label}.${field} is required`)
  }
  if (component.additional_properties === false) {
    assert.deepEqual(Object.keys(value).sort(), Object.keys(value).filter((key) => key in fields).sort(), `${label} has extra fields`)
  }
  for (const [field, fieldValue] of Object.entries(value)) {
    validateScalar(fields[field], fieldValue, `${label}.${field}`)
  }
  return value
}

// Test-only oracle: production modules never import the Backend fixture.
const mockFactoryFromFixture = (componentName, value) => structuredClone(validateComponent(componentName, value))

const {
  CORE_AGENT_ASK,
  CORE_AGENT_RUN,
  CORE_ALARM,
  CORE_APPROVAL,
  CORE_AUDIT_LOG,
  CORE_CHAMBER_GRAPH,
  CORE_DOCUMENT_HIT,
  CORE_PARAMETER,
  CORE_TRACE_POINT,
} = await import('../src/shared/api/contractMocks.js')

const responseFor = (method, url, data, params = {}) => {
  const key = `${method.toUpperCase()} ${url}`
  if (key === 'GET /alarms/paged' && params.equipment_id === 'EQP-PAGE') {
    const page = Number(params.page ?? 1)
    const count = page === 1 ? 100 : page === 2 ? 92 : 0
    return {
      items: Array.from({ length: count }, (_, index) => ({
        ...CORE_ALARM,
        alarm_id: `PAGE-${String((page - 1) * 100 + index + 1).padStart(3, '0')}`,
      })),
      total: 192,
      page,
      size: Number(params.size),
    }
  }
  if (key === 'GET /alarms/R03/R03-DETAIL') {
    return { ...CORE_ALARM, source: 'R03', alarm_id: 'R03-DETAIL' }
  }
  const responses = {
    'GET /alarms': [CORE_ALARM],
    'GET /trace': [CORE_TRACE_POINT],
    'GET /parameters': [CORE_PARAMETER],
    'POST /documents/search': [CORE_DOCUMENT_HIT],
    'GET /relations/chambers/EQP01-PM1': CORE_CHAMBER_GRAPH,
    'GET /agent/runs': [CORE_AGENT_RUN],
    'POST /agent/ask': CORE_AGENT_ASK,
    'GET /approvals': [CORE_APPROVAL],
    'POST /approvals/APR-000001/decision': {
      ...CORE_APPROVAL,
      status: data?.decision ?? CORE_APPROVAL.status,
      decided_by: data?.decided_by ?? null,
      decided_at: data ? '2026-08-04T10:21:10+09:00' : null,
      approved_by: data?.decided_by ?? null,
      approved_at: data ? '2026-08-04T10:21:10+09:00' : null,
    },
    'GET /audit-logs': [CORE_AUDIT_LOG],
    'POST /agent/runs': {
      agent_run_id: 'RUN-000002',
      status: 'RUNNING',
      alarm: data?.alarm ?? { source: 'TRACE', alarm_id: 'TAL-0001' },
    },
    'GET /alarms/paged': { items: [CORE_ALARM], total: 1, page: 1, size: 20 },
    'GET /audit-logs/paged': { items: [CORE_AUDIT_LOG], total: 1, page: 1, size: 20 },
  }
  assert.ok(key in responses, `unexpected transport call: ${key}`)
  return responses[key]
}

const apiClient = (await import('../src/shared/api/client.js')).default
const captures = []
apiClient.defaults.adapter = async (config) => {
  const data = typeof config.data === 'string' ? JSON.parse(config.data) : config.data
  captures.push({ method: config.method.toUpperCase(), url: config.url, params: config.params ?? {}, data })
  if (config.url.endsWith('/MISSING')) {
    const error = new Error('not found')
    error.response = { status: 404 }
    throw error
  }
  if (config.url.endsWith('/BROKEN')) {
    const error = new Error('service unavailable')
    error.response = { status: 503 }
    throw error
  }
  return {
    data: responseFor(config.method, config.url, data, config.params),
    status: config.url === '/agent/runs' && config.method === 'post' ? 202 : 200,
    statusText: 'OK',
    headers: {},
    config,
  }
}

const detection = await import('../src/shared/api/detection.js?shared-client-real')
const knowledge = await import('../src/shared/api/knowledge.js?shared-client-real')
const agent = await import('../src/shared/api/agent.js?shared-client-real')
const analytics = await import('../src/shared/api/analytics.js?shared-client-real')

const transportModules = new Map([
  ['frontend/src/shared/api/detection.js', detection],
  ['frontend/src/shared/api/knowledge.js', knowledge],
  ['frontend/src/shared/api/agent.js', agent],
  ['frontend/src/shared/api/analytics.js', analytics],
])
const parseTransportSymbol = (reference) => {
  const [modulePath, exportName, ...rest] = reference.split('#')
  assert.equal(rest.length, 0, `invalid transport symbol: ${reference}`)
  assert.ok(modulePath && exportName, `invalid transport symbol: ${reference}`)
  const module = transportModules.get(modulePath)
  assert.ok(module, `unregistered transport module: ${modulePath}`)
  return { module, exportName }
}
const transportEntries = aliasRegistry.entries.filter(({ kind }) => kind === 'transport')
assert.ok(transportEntries.length > 0, 'compatibility registry must contain transport entries')
assert.equal(new Set(transportEntries.map(({ id }) => id)).size, transportEntries.length, 'transport ids must be unique')
for (const entry of transportEntries) {
  const current = parseTransportSymbol(entry.symbol)
  assert.equal(typeof current.module[current.exportName], 'function', `${entry.id} current export is missing`)

  if (entry.replacement_kind === 'export') {
    const replacement = parseTransportSymbol(entry.replacement)
    assert.equal(typeof replacement.module[replacement.exportName], 'function', `${entry.id} replacement export is missing`)
  } else {
    assert.match(entry.replacement, /^(GET|POST|PUT|PATCH|DELETE) \/[A-Za-z0-9_/{}/.-]+$/, `${entry.id} endpoint is invalid`)
  }

  if (entry.export_alias_of) {
    const target = parseTransportSymbol(entry.export_alias_of)
    assert.strictEqual(
      current.module[current.exportName],
      target.module[target.exportName],
      `${entry.id} export alias identity drifted`,
    )
  }
}

const realResults = await Promise.all([
  detection.getAlarmsCore({ date_from: undefined, date_to: undefined }),
  detection.getTrace({ lot: 'LOT004', wafer: 'W04', chamber: 'EQP04-PM2', parameter: 'ET_REFL' }),
  detection.getParameters(),
  knowledge.searchDocumentsCore({ query: '포커스 이상 원인', top_k: 4, model_code: undefined }),
  knowledge.getChamberRelationsCore('EQP01-PM1'),
  agent.getRunsCore(),
  agent.askAgent({ question: 'Why was EQP04-PM2 held?' }),
  agent.getApprovalsCore(),
  agent.decideApprovalCanonical('APR-000001', { decision: 'APPROVED', decided_by: 'operator' }),
  analytics.getAuditLogsCore(),
  agent.createRun({ alarm: { source: 'TRACE', alarm_id: 'TAL-0001' } }),
])

assert.equal(captures.length, 11)
assert.deepEqual(
  captures.map(({ method, url }) => `${method} ${url}`),
  coreOperations.map(({ method, path }) => `${method} ${path.replace('{chamber_id}', 'EQP01-PM1').replace('{approval_id}', 'APR-000001')}`),
  'shared clients must cover the exact core operation order',
)
assert.ok(realResults.slice(0, 4).every(Array.isArray))
assert.ok(Array.isArray(realResults[5]))
assert.ok(Array.isArray(realResults[7]))
assert.ok(Array.isArray(realResults[9]))
assert.deepEqual(captures[0].params, {}, 'undefined query fields must be removed')
assert.deepEqual(Object.keys(captures[3].data).sort(), ['query', 'top_k'])
assert.deepEqual(captures[8].data, { decision: 'APPROVED', decided_by: 'operator' })
assert.deepEqual(captures[10].data, { alarm: { source: 'TRACE', alarm_id: 'TAL-0001' } })

await agent.decideApproval('APR-000001', {
  decision: 'REJECT',
  decided_by: 'operator',
  decision_comment: '현장 확인 결과 반려',
})
assert.deepEqual(captures.at(-1).data, {
  decision: 'REJECTED',
  decided_by: 'operator',
  decision_comment: '현장 확인 결과 반려',
})

const pagedResults = await Promise.all([
  detection.getAlarms(),
  agent.getRuns(),
  agent.getApprovals(),
  analytics.getAuditLogsPaged(),
])
assert.deepEqual(
  captures.slice(-4).map(({ method, url }) => `${method} ${url}`),
  ['GET /alarms/paged', 'GET /agent/runs', 'GET /approvals', 'GET /audit-logs/paged'],
)
assert.ok(!captures.some(({ url }) => url === '/agent/runs/paged'))
for (const result of pagedResults) {
  assert.deepEqual(Object.keys(result).sort(), ['items', 'page', 'size', 'total'])
  assert.ok(Array.isArray(result.items))
}

const allAlarmsCaptureStart = captures.length
const allAlarms = await detection.getAllAlarms({ area: 'ETCH', equipment_id: 'EQP-PAGE' })
const allAlarmsCaptures = captures.slice(allAlarmsCaptureStart)
assert.equal(allAlarms.items.length, 192)
assert.equal(allAlarms.total, 192)
assert.deepEqual(allAlarmsCaptures.map(({ params }) => params.page), [1, 2])
assert.ok(
  allAlarmsCaptures.every(
    ({ params }) => params.area === 'ETCH' && params.equipment_id === 'EQP-PAGE' && params.size === 100,
  ),
  '전체 페이지 순회 중 scope filter와 page size를 유지해야 합니다',
)

const sourceAlarm = await detection.getAlarm('R03-DETAIL', 'R03')
assert.equal(captures.at(-1).url, '/alarms/R03/R03-DETAIL')
assert.equal(sourceAlarm.source, 'R03')
assert.equal(await detection.getAlarm('MISSING', 'TRACE'), null)
await assert.rejects(() => detection.getAlarm('BROKEN', 'SUMMARY'), /service unavailable/)

const legacyGraph = await knowledge.getChamberRelations('EQP01-PM1')
assert.equal(captures.at(-1).url, '/relations/chambers/EQP01-PM1')
assert.equal(legacyGraph.chamber.chamber_id, CORE_CHAMBER_GRAPH.context.chamber_id)
assert.equal(legacyGraph.equipment.equipment_id, CORE_CHAMBER_GRAPH.context.equipment_id)

assert.throws(
  () => agent.createRun({ alarm: { source: 'TRACE', alarm_id: 'TAL-0001', extra: true } }),
  /unknown fields/,
)
assert.throws(
  () => agent.decideApprovalCanonical('APR-000001', { decision: 'APPROVE', decided_by: 'operator' }),
  /APPROVED or REJECTED/,
)
assert.throws(
  () => agent.decideApproval('APR-000001', { decision: 'REJECTED', decided_by: 'operator' }),
  /APPROVE or REJECT/,
)
assert.throws(
  () => agent.decideApprovalCanonical('APR-000001', { decision: 'APPROVED', decided_by: 'operator', extra: true }),
  /unknown fields/,
)

process.env.VITE_USE_MOCK = 'true'
const detectionMock = await import('../src/shared/api/detection.js?shared-client-mock')
const knowledgeMock = await import('../src/shared/api/knowledge.js?shared-client-mock')
const agentMock = await import('../src/shared/api/agent.js?shared-client-mock')
const analyticsMock = await import('../src/shared/api/analytics.js?shared-client-mock')

const mockResults = await Promise.all([
  detectionMock.getAlarmsCore(),
  detectionMock.getTrace({ lot: 'LOT004', wafer: 'W04', chamber: 'EQP04-PM2', parameter: 'ET_REFL' }),
  detectionMock.getParameters(),
  knowledgeMock.searchDocumentsCore({ query: '포커스 이상 원인' }),
  knowledgeMock.getChamberRelationsCore('EQP01-PM1'),
  agentMock.getRunsCore(),
  agentMock.askAgent({ question: 'Why was EQP04-PM2 held?' }),
  agentMock.getApprovalsCore(),
  agentMock.decideApprovalCanonical('APR-000001', { decision: 'REJECTED', decided_by: 'operator' }),
  analyticsMock.getAuditLogsCore(),
  agentMock.createRun({ alarm: { source: 'R03', alarm_id: 'R03-f41e6518529e8ed5e6a9' } }),
])

for (const [component, value] of [
  ['AlarmItem', mockResults[0][0]],
  ['TracePoint', mockResults[1][0]],
  ['ParameterItem', mockResults[2][0]],
  ['DocumentHit', mockResults[3][0]],
  ['ChamberGraphResponse', mockResults[4]],
  ['AgentRunItem', mockResults[5][0]],
  ['AgentAskResponse', mockResults[6]],
  ['ApprovalItem', mockResults[7][0]],
  ['ApprovalItem', mockResults[8]],
  ['AuditLogItem', mockResults[9][0]],
  ['AgentRunAccepted', mockResults[10]],
]) {
  mockFactoryFromFixture(component, value)
}

const alarmMock = mockResults[0][0]
assert.equal(alarmMock.equipment, alarmMock.equipment_id)
assert.equal(alarmMock.chamber, alarmMock.chamber_id)
assert.equal(alarmMock.recipe, alarmMock.recipe_id)
assert.equal(alarmMock.lot, alarmMock.lot_id)
assert.equal(alarmMock.wafer, alarmMock.wafer_id)
assert.equal(alarmMock.parameter, alarmMock.parameter_id)
assert.equal(alarmMock.step_no, alarmMock.recipe_step_no)
assert.equal(alarmMock.fault, alarmMock.predicted_fault_code)
assert.equal(alarmMock.notify, alarmMock.notify_status === 'SENT')
assert.equal(alarmMock.mes, alarmMock.mes_status ?? '')

const parameterMock = mockResults[2][0]
assert.equal(parameterMock.name, parameterMock.parameter_name)
assert.equal(parameterMock.LSL, parameterMock.spec_lower)
assert.equal(parameterMock.LCL, parameterMock.ctrl_lower)
assert.equal(parameterMock.TARGET, parameterMock.target_value)
assert.equal(parameterMock.UCL, parameterMock.ctrl_upper)
assert.equal(parameterMock.USL, parameterMock.spec_upper)
assert.equal(mockResults[3][0].doc_id, mockResults[3][0].document_id)

const runMock = mockResults[5][0]
assert.equal(runMock.chamber, runMock.chamber_id)
assert.equal(runMock.fault_code, runMock.predicted_fault_code)
assert.equal(runMock.fault_name, null)
assert.equal(runMock.fault_color, null)
for (const tool of runMock.tools) {
  assert.equal(tool.n, tool.tool_name)
  assert.equal(tool.s, tool.status)
}

for (const approvalMock of [mockResults[7][0], mockResults[8]]) {
  assert.equal(approvalMock.lot, approvalMock.lot_id)
  assert.equal(approvalMock.equipment, approvalMock.equipment_id)
  assert.equal(approvalMock.chamber, approvalMock.chamber_id)
  assert.equal(approvalMock.fault_code, approvalMock.predicted_fault_code)
  assert.equal(approvalMock.approved_by, approvalMock.decided_by)
  assert.equal(approvalMock.approved_at, approvalMock.decided_at)
}

const auditMock = mockResults[9][0]
assert.equal(auditMock.at, auditMock.occurred_at)
assert.equal(auditMock.actor, auditMock.actor_type)
assert.equal(auditMock.entity, `${auditMock.entity_type}:${auditMock.entity_id}`)
assert.equal(auditMock.event, auditMock.after.status === 'APPROVED' ? 'APPROVE' : 'REJECT')

const askMock = mockResults[6]
for (const tool of askMock.tools) {
  assert.equal(tool.name, tool.tool_name)
  assert.equal(tool.result, tool.result_summary)
}
assert.equal(askMock.evidence.doc_id, askMock.evidence.document_id)
assert.equal(askMock.evidence.document_id, askMock.evidence_items[0].document_id)
assert.equal(askMock.evidence.chunk_id, askMock.evidence_items[0].chunk_id)
assert.equal(askMock.limit, askMock.limitations.join('; '))

const aliases = {
  AlarmItem: ['equipment', 'chamber', 'recipe', 'lot', 'wafer', 'parameter', 'step_no', 'fault', 'notify', 'mes'],
  ParameterItem: ['name', 'LSL', 'LCL', 'TARGET', 'UCL', 'USL'],
  DocumentHit: ['doc_id'],
  AgentRunItem: ['chamber', 'fault_code', 'fault_name', 'fault_color'],
  ApprovalItem: ['lot', 'equipment', 'chamber', 'fault_code', 'approved_by', 'approved_at'],
  AuditLogItem: ['at', 'actor', 'event', 'entity'],
  AgentAskResponse: ['evidence', 'limit'],
}
const canonicalWitness = {
  AlarmItem: 'equipment_id',
  ParameterItem: 'parameter_name',
  DocumentHit: 'document_id',
  AgentRunItem: 'chamber_id',
  ApprovalItem: 'lot_id',
  AuditLogItem: 'occurred_at',
  AgentAskResponse: 'evidence_items',
}
const projectionCases = [
  ['AlarmItem', mockResults[0][0], 'projectAlarm'],
  ['ParameterItem', mockResults[2][0], 'projectParameter'],
  ['DocumentHit', mockResults[3][0], 'projectDocumentHit'],
  ['AgentRunItem', mockResults[5][0], 'projectAgentRun'],
  ['ApprovalItem', mockResults[7][0], 'projectApproval'],
  ['AuditLogItem', mockResults[9][0], 'projectAuditLog'],
  ['AgentAskResponse', mockResults[6], 'projectAgentAsk'],
]
const projections = await import('../src/shared/api/projections.js')
const compatibilityAliases = new Map()
for (const operation of fixture.operations) {
  for (const rule of operation.compatibility) {
    const aliases = compatibilityAliases.get(rule.component) ?? new Set()
    rule.aliases.forEach((alias) => aliases.add(alias))
    compatibilityAliases.set(rule.component, aliases)
  }
}
for (const [component, fields] of Object.entries(projections.CANONICAL_FIELDS)) {
  assert.ok(components[component]?.fields, `${component} must be backed by the Backend fixture`)
  const aliases = compatibilityAliases.get(component) ?? new Set()
  const expected = Object.keys(components[component].fields).filter((field) => !aliases.has(field))
  assert.deepEqual([...fields].sort(), expected.sort(), `${component} canonical fields drifted`)
}
for (const [component, value, projectionName] of projectionCases) {
  const withoutAliases = structuredClone(value)
  for (const alias of aliases[component]) delete withoutAliases[alias]
  if (component === 'AgentRunItem') {
    for (const tool of withoutAliases.tools) {
      delete tool.n
      delete tool.s
    }
  }
  if (component === 'AgentAskResponse') {
    for (const tool of withoutAliases.tools) {
      delete tool.name
      delete tool.result
    }
  }
  assert.deepEqual(projections[projectionName](value), projections[projectionName](withoutAliases))
  const aliasOnlyMutation = structuredClone(value)
  delete aliasOnlyMutation[canonicalWitness[component]]
  assert.throws(() => projections[projectionName](aliasOnlyMutation), /missing canonical fields/)
}

const askWithNestedAlias = structuredClone(mockResults[6])
askWithNestedAlias.evidence_items[0].doc_id = askWithNestedAlias.evidence_items[0].document_id
assert.deepEqual(projections.projectAgentAsk(askWithNestedAlias), projections.projectAgentAsk(mockResults[6]))
assert.ok(!Object.hasOwn(projections.projectAgentAsk(askWithNestedAlias).evidence_items[0], 'doc_id'))
delete askWithNestedAlias.evidence_items[0].document_id
assert.throws(() => projections.projectAgentAsk(askWithNestedAlias), /missing canonical fields/)

const runWithNestedAlias = structuredClone(mockResults[5][0])
runWithNestedAlias.deliveries[0].legacy_status = runWithNestedAlias.deliveries[0].status
assert.deepEqual(projections.projectAgentRun(runWithNestedAlias), projections.projectAgentRun(mockResults[5][0]))
assert.ok(!Object.hasOwn(projections.projectAgentRun(runWithNestedAlias).deliveries[0], 'legacy_status'))
delete runWithNestedAlias.deliveries[0].status
assert.throws(() => projections.projectAgentRun(runWithNestedAlias), /missing canonical fields/)

const serializedMocks = JSON.stringify(mockResults)
assert.ok(!serializedMocks.includes('ground_truth_fault_code'))
assert.ok(!serializedMocks.includes('lot_history.fault_code'))

console.log(
  `shared-client-contract: 11 core transports, ${transportEntries.length} compatibility transports, run bare adapter, exact mocks, canonical projections passed`,
)
