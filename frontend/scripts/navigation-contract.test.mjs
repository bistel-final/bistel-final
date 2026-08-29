import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

import { PRIMARY_MENUS } from '../src/app/navigation.js'

const EXPECTED_MENUS = [
  { to: '/dashboard', label: '알람 대시보드' },
  { to: '/alarms', label: '알람 히스토리' },
  { to: '/agent-runs', label: 'Agent 분석 · 승인' },
  { to: '/documents', label: '문서 검색' },
  { to: '/ontology', label: '온톨로지' },
  { to: '/analytics', label: '자연어 분석' },
  { to: '/audit-logs', label: '감사로그' },
]
const EXPECTED_ROUTE_PATHS = [
  '/',
  'dashboard',
  'alarms',
  'alarms/:alarmId',
  'agent-runs',
  'agent-runs/:runId',
  'documents',
  'ontology',
  'analytics',
  'audit-logs',
  'knowledge',
  '*',
]

assert.deepEqual(PRIMARY_MENUS, EXPECTED_MENUS, '주 navigation 7개 순서·라벨·경로 불일치')
assert.equal(new Set(PRIMARY_MENUS.map(({ to }) => to)).size, PRIMARY_MENUS.length, '중복 navigation 경로')

const routeSource = await readFile(new URL('../src/app/routes.jsx', import.meta.url), 'utf8')
const pathTokens = [...routeSource.matchAll(/\bpath\s*:/g)]
const literalPaths = [...routeSource.matchAll(/\bpath\s*:\s*(['"])([^'"]+)\1/g)].map((match) => match[2])

assert.ok(pathTokens.length > 0, 'routes.jsx에서 path를 하나도 찾지 못했습니다')
assert.equal(literalPaths.length, pathTokens.length, '동적·비문자열 route path는 허용하지 않습니다')
assert.equal(new Set(literalPaths).size, literalPaths.length, '중복 route path는 허용하지 않습니다')
assert.deepEqual(literalPaths, EXPECTED_ROUTE_PATHS, 'route path 정확한 집합·순서 불일치')
assert.ok(!PRIMARY_MENUS.some(({ to }) => to === '/knowledge'), '/knowledge는 호환 route일 뿐 주 navigation이 아닙니다')
assert.ok(!literalPaths.includes('traces') && !literalPaths.includes('actions'), '/traces·/actions route는 제거 상태여야 합니다')

console.log('navigation-contract: 7개 주 navigation과 호환·상세 route 계약 통과')
