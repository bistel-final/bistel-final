// 필터 기본값 — 컴포넌트 파일(fast refresh 제약) 밖으로 분리한 상수 모듈
export const ALL = '전체'

// from/to 기본값을 특정 날짜로 고정하지 않는다 — mock 시연용 픽스처(2026-06-01~04)는
// 실 데이터의 실제 발생 기간과 무관하다. 빈 문자열은 scopedAlarms()/inRange()에서
// "그 경계 미지정"으로 해석되어 기간 필터 없이 전체를 보여준다.
export const DEFAULT_SCOPE = {
  from: '',
  to: '',
  area: ALL,
  equipment: ALL,
  chamber: ALL,
}

// 화면 사이(대시보드 KPI → 알람 히스토리)로 넘기는 필터 키
export const SCOPE_KEYS = Object.freeze(['from', 'to', 'area', 'equipment', 'chamber'])

// 적용된 필터를 쿼리스트링으로 — 미지정(''·전체)은 키를 지워 URL을 짧게 유지한다.
// base 에 기존 쿼리(URLSearchParams·객체)를 주면 그 위에 덮어쓴다(tab·source 보존).
export function scopeToParams(scope, base) {
  const params = new URLSearchParams(base ?? undefined)
  for (const key of SCOPE_KEYS) {
    const value = scope?.[key]
    if (value == null || value === '' || value === ALL) params.delete(key)
    else params.set(key, String(value))
  }
  return params
}

// 쿼리에 실린 필터만 읽는다 — 하나도 없으면 null(= 화면 기본 동작 유지).
export function scopeFromParams(searchParams) {
  const picked = {}
  for (const key of SCOPE_KEYS) {
    const value = searchParams?.get(key)
    if (value) picked[key] = value
  }
  return Object.keys(picked).length ? { ...DEFAULT_SCOPE, ...picked } : null
}
