// 조치 목록 정렬 — 화면(ActionsPage)과 테스트(scripts/actions-sort.test.mjs)가
// 같은 함수를 쓴다 (복사 금지). JSX 없이 순수 JS로 두어 node에서도 import 가능.
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
