const DEFAULT_RUN_ERROR = '분석 실행 요청에 실패했습니다.'

const responseMessage = (error) => {
  const data = error?.response?.data
  for (const value of [data?.message, data?.detail]) {
    if (typeof value === 'string' && value.trim()) return value
  }
  return null
}

// FastAPI 422 detail은 객체 배열일 수 있으므로 문자열만 화면에 전달한다.
export function runErrorMessage(error) {
  const status = error?.response?.status
  const message = responseMessage(error)
  if (status === 409) return message ?? '이 incident에는 기존 분석 실행이 있습니다.'
  if (status === 422) return message ?? '분석을 실행할 수 없는 알람입니다.'
  if (status === 503) return message ?? 'Agent 실행 서비스를 사용할 수 없습니다.'
  return message ?? DEFAULT_RUN_ERROR
}

export function analysisActionOf(alarm) {
  const runId = typeof alarm?.latest_agent_run_id === 'string' ? alarm.latest_agent_run_id.trim() : ''
  if (!runId) return { mode: 'CREATE', label: '분석 실행', runId: null }

  const status = alarm?.agent_run_status
  const label = ['RUNNING', 'WAITING_APPROVAL'].includes(status)
    ? '진행 중인 분석 보기'
    : status === 'FAILED'
      ? '실패 분석 보기'
      : '분석 결과 보기'
  return { mode: 'OPEN', label, runId }
}

export function periodLabel({ from, to }) {
  if (from && to) return `${from} ~ ${to}`
  if (from) return `${from} 이후`
  if (to) return `${to} 이전`
  return '전체 기간'
}

export function partitionAlarms(alarms) {
  const rows = [...alarms].sort(
    (a, b) => b.occurred_at.localeCompare(a.occurred_at) || b.alarm_id.localeCompare(a.alarm_id),
  )
  return {
    trace: rows.filter((alarm) => alarm.source === 'TRACE'),
    summary: rows.filter((alarm) => alarm.source === 'SUMMARY'),
    r03: rows.filter((alarm) => alarm.source === 'R03'),
  }
}

export const hasDashboardResults = (aggregate) => Boolean(aggregate && aggregate.total > 0)
