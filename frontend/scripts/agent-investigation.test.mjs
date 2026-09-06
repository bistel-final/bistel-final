import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import {
  COMPARISON_STATES, excursionPercent, investigationTimelineState,
} from '../src/features/agent/investigation-view.js'

const fixture = JSON.parse(readFileSync(new URL('../../backend/tests/fixtures/v5_c_7_1/react_trace_public.json', import.meta.url)))
assert.deepEqual(fixture.cases.map((detail) => investigationTimelineState(detail).phase), [
  'pending', 'pending', 'success', 'success', 'hidden', 'unavailable',
])
assert.equal(investigationTimelineState({ autonomy_level: 3, trace_state: 'AVAILABLE', react_trace: [] }).phase, 'empty')
assert.equal(investigationTimelineState(null).phase, 'hidden')
assert.equal(excursionPercent(3), '300%')
assert.equal(excursionPercent(null), '계산 불가')
assert.equal(excursionPercent(Infinity), '계산 불가')
assert.notEqual(COMPARISON_STATES.NOT_CHECKED, COMPARISON_STATES.NOT_AVAILABLE)
assert.notEqual(COMPARISON_STATES.CHECKED, COMPARISON_STATES.NOT_CHECKED)
const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
try {
  const { default: Timeline } = await server.ssrLoadModule('/src/features/agent/components/RunInvestigationTimeline.jsx')
  const { default: Card } = await server.ssrLoadModule('/src/features/agent/components/RunInvestigationCard.jsx')
  for (const detail of fixture.cases) {
    const html = renderToStaticMarkup(React.createElement(Timeline, { detail }))
    if (detail.trace_state === 'PENDING') assert.match(html, /실행 종료 후/)
    if (detail.trace_state === 'UNAVAILABLE') assert.match(html, /저장된 조사 이력이 없습니다/)
    if (detail.trace_state === 'AVAILABLE') assert.match(html, /조사 타임라인/)
    assert.doesNotMatch(html, /argument_digest|llm_model|lot_hist_id/)
  }
  const diagnosis = { status: 'AVAILABLE', parameter_findings: [{
    parameter_id: 'PH_FOCUS', step_no: 1, direction: 'BOTH', excursion_ratio: 3, wafer_scope: 'SINGLE',
  }], origin_assessment: { scope: 'CURRENT_CHAMBER', basis: [], compared: {
    upstream: 'NOT_AVAILABLE', downstream: 'NOT_CHECKED', sibling: 'CHECKED', history: 'CHECKED', metrology: 'NOT_CHECKED',
  } } }
  const html = renderToStaticMarkup(React.createElement(Card, { diagnosis }))
  for (const text of ['양방향 이탈', '300%', '확인', '미확인', '대상 없음']) assert.ok(html.includes(text))
} finally {
  await server.close()
}
console.log('OK agent-investigation: shared trace fixture, pending/terminal, comparison and ratio contracts')
