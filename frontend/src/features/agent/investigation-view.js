export const COMPARISON_LABELS = Object.freeze({
  upstream: '상류', downstream: '하류', sibling: '형제 챔버',
  history: '이전 lot 이력', metrology: '계측 결과',
})

export const ORIGIN_LABELS = Object.freeze({
  UPSTREAM: '상류 공정', DOWNSTREAM: '하류 공정', CURRENT_CHAMBER: '현재 챔버',
  EQUIPMENT_COMMON: '설비 공통', UNDETERMINED: '소재 미확정',
})

export const COMPARISON_STATES = Object.freeze({
  CHECKED: '확인', NOT_CHECKED: '미확인', NOT_AVAILABLE: '대상 없음',
})

export const TOOL_LABELS = Object.freeze({
  get_fdc_summary: 'FDC 조회', get_chamber_parameter_history: '챔버 이력·대조 조회',
  get_metrology_result: '계측 결과 조회', get_equipment_context: '설비 컨텍스트',
  search_documents: '문서 검색', stop: '조사 종료',
})

export const STOP_LABELS = Object.freeze({
  LLM_STOP: '근거 수집 완료', STEP_CAP: '선택 횟수 상한 도달',
  BUDGET_EXHAUSTED: '조회 예산 소진', GUARD_LIMIT: '선택 검증 상한 도달',
  REACT_STRUCTURE_INVALID: '응답 구조 오류', LLM_TIMEOUT: '응답 시간 초과',
  LLM_DEPENDENCY: '모델 연결 오류',
})

export function investigationTimelineState(detail) {
  if (!detail || detail.autonomy_level !== 3) return { phase: 'hidden', message: '' }
  if (detail.trace_state === 'PENDING') return {
    phase: 'pending', message: '실행 종료 후 조사 이력이 표시됩니다. 현재 이력은 아직 확정되지 않았습니다.',
  }
  if (detail.trace_state === 'UNAVAILABLE') return {
    phase: 'unavailable', message: '실행이 종료되었지만 저장된 조사 이력이 없습니다.',
  }
  if (detail.trace_state !== 'AVAILABLE' || !detail.react_trace?.length) return {
    phase: 'empty', message: '표시할 조사 이력이 없습니다.',
  }
  return { phase: 'success', message: '' }
}

export function excursionPercent(value) {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${(value * 100).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}%`
    : '계산 불가'
}
