// 조치 목록 정렬·탭 규칙 — 화면(ActionsPage)과 테스트(scripts/actions-sort.test.mjs)가
// 같은 함수를 쓴다 (복사 금지). JSX 없이 순수 JS로 두어 node에서도 import 가능.
//
// 필드는 공개 ActionItem 기준이다. 전송 상태는 `deliveries[]`가 유일한 정본이다.
//
// 규칙 (디자인 v2 §5 조치 목록):
// 1) approval_status PENDING 행이 최상단 고정 — 그 안에서 created_at 내림차순
// 2) 나머지는 created_at 내림차순
// created_at은 "YYYY-MM-DD HH:mm[:ss]" 또는 ISO("YYYY-MM-DDTHH:mm:ss+09:00") —
// 둘 다 자리수 고정 포맷이라 문자열 비교가 곧 시간 비교다 (Date 파싱 불필요).

const rank = (a) => (a.approval_status === 'PENDING' ? 0 : 1)

export function sortActions(items) {
  return [...items].sort(
    (a, b) =>
      rank(a) - rank(b) ||
      String(b.created_at).localeCompare(String(a.created_at)) ||
      // 동시각 안전장치 — ID 내림차순으로 순서를 고정한다
      String(b.action_id).localeCompare(String(a.action_id)),
  )
}

// 탭 → GET /actions 서버 파라미터.
// 승인 대기만 approval_status, 완료·전송실패·진행중은 send_status, 전체는 파라미터 없음.
export function tabParams(key) {
  if (key === 'ALL') return {}
  if (key === 'PENDING') return { approval_status: 'PENDING' }
  return { send_status: key }
}

// 같은 판정을 클라이언트에서도 쓴다 (탭 배지 건수 계산 — 서버 응답 1회를 다섯 탭으로 나눈다)
export function matchTab(a, key) {
  if (key === 'ALL') return true
  if (key === 'PENDING') return a.approval_status === 'PENDING'
  return (a.deliveries ?? []).some((delivery) => delivery.status === key)
}
