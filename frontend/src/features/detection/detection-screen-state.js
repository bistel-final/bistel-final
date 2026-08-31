const DEFAULT_RUN_ERROR = '분석 실행 요청에 실패했습니다.'

const detailMessage = (error) => {
  const detail = error?.response?.data?.detail
  return typeof detail === 'string' && detail.trim() ? detail : null
}

// FastAPI 422 detail은 객체 배열일 수 있으므로 문자열만 화면에 전달한다.
export function runErrorMessage(error) {
  const status = error?.response?.status
  const detail = detailMessage(error)
  if (status === 409) return detail ?? '이 incident는 이미 분석이 진행 중입니다.'
  if (status === 422) return detail ?? '분석을 실행할 수 없는 알람입니다.'
  if (status === 503) return detail ?? 'Agent 실행 서비스를 사용할 수 없습니다.'
  return detail ?? DEFAULT_RUN_ERROR
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
