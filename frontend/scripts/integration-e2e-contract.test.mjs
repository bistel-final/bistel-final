import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

process.env.VITE_USE_MOCK = 'false'
for (const domain of ['DETECTION', 'AGENT', 'KNOWLEDGE', 'ANALYTICS']) {
  delete process.env[`VITE_USE_MOCK_${domain}`]
}

const apiClient = (await import('../src/shared/api/client.js')).default
const detection = await import('../src/shared/api/detection.js?cm-5-2-real')
const agent = await import('../src/shared/api/agent.js?cm-5-2-real')
const knowledge = await import('../src/shared/api/knowledge.js?cm-5-2-real')
const analytics = await import('../src/shared/api/analytics.js?cm-5-2-real')

const contractFixtureUrls = [
  new URL('../../backend/tests/fixtures/v5_cm_4_4/api_contract_baseline.json', import.meta.url),
  new URL('../../backend/tests/fixtures/v5_cm_4_4/api_contract_optional.json', import.meta.url),
]
const contractOperations = (
  await Promise.all(contractFixtureUrls.map(async (url) => JSON.parse(await readFile(url, 'utf8')).operations))
).flat()

const escapeRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const operationIsDeclared = (actualOperation) => {
  const separator = actualOperation.indexOf(' ')
  const method = actualOperation.slice(0, separator)
  const path = actualOperation.slice(separator + 1)
  return contractOperations.some((operation) => {
    if (operation.method !== method) return false
    const pathPattern = operation.path
      .split(/(\{[^}]+\})/)
      .map((part) => (part.startsWith('{') ? '[^/]+' : escapeRegex(part)))
      .join('')
    return new RegExp(`^${pathPattern}$`).test(path)
  })
}

const requests = [
  ['dashboard', 'GET /dashboard/summary', () => detection.getDashboard()],
  ['alarms', 'GET /alarms/paged', () => detection.getAlarms()],
  ['agent-runs', 'GET /agent/runs', () => agent.getRunsCore()],
  [
    'documents',
    'POST /documents/search',
    () => knowledge.searchDocumentsCore({ query: '포커스 이상', top_k: 4 }),
  ],
  [
    'ontology',
    'GET /relations/chambers/EQP01-PM1',
    () => knowledge.getChamberRelationsCore('EQP01-PM1'),
  ],
  ['analytics', 'POST /analytics/query', () => analytics.postQuery('설비별 알람 수')],
  ['audit-logs', 'GET /audit-logs/paged', () => analytics.getAuditLogsPaged()],
]

for (const [, operation] of requests) {
  assert.equal(
    operationIsDeclared(operation),
    true,
    `${operation} is not declared in the Backend baseline or optional API fixture`,
  )
}

const responseData = (url) => {
  const page = { items: [], total: 0, page: 1, size: 20 }
  return {
    '/dashboard/summary': { alarm_count: 0, recent_alarms: [] },
    '/alarms/paged': page,
    '/agent/runs': [],
    '/documents/search': [],
    '/relations/chambers/EQP01-PM1': {},
    '/analytics/query': {
      generated_sql: null,
      is_rejected: true,
      reject_reason: 'POLICY_REJECTED',
    },
    '/audit-logs/paged': page,
  }[url]
}

const okResponse = (config) => ({
  data: responseData(config.url),
  status: 200,
  statusText: 'OK',
  headers: {},
  config,
})

// production 전역 false에서 7개 화면의 대표 요청이 실제 transport까지 도달해야 한다.
const successCalls = []
apiClient.defaults.adapter = async (config) => {
  successCalls.push(`${config.method.toUpperCase()} ${config.url}`)
  return okResponse(config)
}
await Promise.all(requests.map(([, , invoke]) => invoke()))
assert.deepEqual(
  successCalls,
  requests.map(([, operation]) => operation),
  '전역 mock=false에서 화면 대표 요청이 real API branch를 타야 합니다',
)

// 지연 주입: 모든 요청이 transport에 걸려 있는 동안 어느 것도 success로 끝나지 않는다.
let releaseDelay
const delayGate = new Promise((resolve) => {
  releaseDelay = resolve
})
let delayedStarted = 0
apiClient.defaults.adapter = async (config) => {
  delayedStarted += 1
  await delayGate
  return okResponse(config)
}
let delaySettled = false
const delayed = Promise.all(requests.map(([, , invoke]) => invoke())).then(() => {
  delaySettled = true
})
await new Promise((resolve) => setImmediate(resolve))
assert.equal(delayedStarted, requests.length, 'mock branch는 transport 지연 주입을 우회합니다')
assert.equal(delaySettled, false, '지연 요청이 해제 전에 완료되면 Loading 증적을 만들 수 없습니다')
releaseDelay()
await delayed
assert.equal(delaySettled, true)

const rejectedBy = async (errorFactory) => {
  apiClient.defaults.adapter = async () => {
    throw errorFactory()
  }
  return Promise.allSettled(requests.map(([, , invoke]) => invoke()))
}

// network failure와 4xx는 성공 payload로 변환하지 않고 각 화면 경계까지 reject한다.
const networkResults = await rejectedBy(() => Object.assign(new Error('network unavailable'), { code: 'ERR_NETWORK' }))
assert.ok(networkResults.every((result) => result.status === 'rejected' && result.reason.code === 'ERR_NETWORK'))

const validationResults = await rejectedBy(() =>
  Object.assign(new Error('request rejected'), { response: { status: 422, data: { code: 'VALIDATION_ERROR' } } }),
)
assert.ok(validationResults.every((result) => result.status === 'rejected' && result.reason.response?.status === 422))

// 화면별 4상태 표현은 source 계약으로 고정한다. 실행일에는 runbook의 network 증적을 별도로 남긴다.
const stateContracts = {
  '../src/features/detection/pages/DashboardPage.jsx': [
    /LoadingState/,
    /ErrorState/,
    /total: rows\.length/,
    /DashCharts/,
  ],
  '../src/features/detection/pages/AlarmsPage.jsx': [
    /LoadingState/,
    /ErrorState/,
    /조건에 맞는 알람이 없습니다/,
    /AlarmTracePanel/,
  ],
  '../src/features/agent/pages/AgentRunPage.jsx': [
    /phase === 'loading'/,
    /phase === 'error'/,
    /phase === 'empty'/,
    /phase !== 'success'/,
  ],
  '../src/features/knowledge/pages/DocumentsPage.jsx': [
    /loading \? '검색 중/,
    /ErrorState/,
    /검색 결과가 없습니다/,
    /DocumentSearchResultCard/,
  ],
  '../src/features/knowledge/pages/OntologyPage.jsx': [
    /status === 'loading'/,
    /status === 'error'/,
    /표시 가능한 관계가 없습니다/,
    /<OntologyGraphCanvas/,
  ],
  '../src/features/analytics/pages/AnalyticsPage.jsx': [
    /phase === 'gen'/,
    /phase === 'failed'/,
    /phase === 'unknown'/,
    /phase === 'done'/,
  ],
  '../src/features/analytics/pages/AuditLogPage.jsx': [
    /LoadingState/,
    /ErrorState/,
    /조건에 맞는 감사 이벤트가 없습니다/,
    /AuditTable/,
  ],
}

for (const [relativePath, patterns] of Object.entries(stateContracts)) {
  const source = await readFile(new URL(relativePath, import.meta.url), 'utf8')
  for (const pattern of patterns) {
    assert.match(source, pattern, `${relativePath}의 Loading·Error·Empty·Success 계약이 빠졌습니다`)
  }
}

// 화면이 API 결과 본문을 mock fixture에서 직접 가져오는 우회는 금지한다. 검색 chip·filter는
// 표시용 입력이라 허용한다. NL_INITIAL_HISTORY는 V5-D-2.6 머지로 실제 이력 hydrate가
// 들어오면서 제거됐다. 이 allowlist가 늘어나면 1단 CI부터 red다.
const primaryPageImports = new Map([
  ['../src/features/detection/pages/DashboardPage.jsx', []],
  ['../src/features/detection/pages/AlarmsPage.jsx', []],
  ['../src/features/agent/pages/AgentRunPage.jsx', []],
  ['../src/features/knowledge/pages/DocumentsPage.jsx', ['DOC_CHIPS', 'DOC_FILTERS']],
  ['../src/features/knowledge/pages/OntologyPage.jsx', []],
  ['../src/features/analytics/pages/AnalyticsPage.jsx', ['NL_CHIPS']],
  ['../src/features/analytics/pages/AuditLogPage.jsx', []],
])
const deferredRuntimeImports = []
for (const [relativePath, expectedNames] of primaryPageImports) {
  const source = await readFile(new URL(relativePath, import.meta.url), 'utf8')
  const importedNames = []
  for (const match of source.matchAll(/import\s*\{([^}]+)\}\s*from\s*['"][^'"]+\/mock\/[^'"]+['"]/g)) {
    importedNames.push(...match[1].split(',').map((name) => name.trim()).filter(Boolean))
  }
  assert.deepEqual(importedNames.sort(), [...expectedNames].sort(), `${relativePath} mock import allowlist drift`)
  if (importedNames.includes('NL_INITIAL_HISTORY')) deferredRuntimeImports.push('V5-D-2.6:NL_INITIAL_HISTORY')
}
// V5-D-2.6 머지 후에는 지연 mock import가 남아 있지 않아야 한다.
assert.deepEqual(deferredRuntimeImports, [])

console.log('OK integration E2E contract: Backend fixture · mock=false real transport · delay/network/422 · 7-screen states')
