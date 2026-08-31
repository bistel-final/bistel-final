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
