// 상태→스타일 매핑 — README "상태→색 매핑 (전 화면 공통, 고정)" 그대로.
// 6화면이 이 함수를 공유한다. 화면 개별 색 지정 금지.

// 공개 조치코드: EQP_HOLD 적 · WARNING 황 · MONITORING 녹 (틴트)
export const actionCodeVariant = (code) =>
  code === 'EQP_HOLD' ? 't-red' : code === 'WARNING' ? 't-amber' : 't-green'

// Agent run 상태: 의미색은 유지하되 눈부신 solid 대신 저채도 tint를 쓴다.
export const runStatusVariant = (status) =>
  status === 'RUNNING'
    ? 't-blue'
    : status === 'WAITING_APPROVAL'
      ? 't-amber'
      : status === 'COMPLETED'
        ? 't-green'
        : status === 'FAILED'
          ? 't-red'
          : 't-gray'

// 룰: R03만 적색 tint, R01/R02 회색 tint
export const ruleVariant = (rule) => (String(rule).startsWith('R03') ? 't-red' : 't-gray')

// 판정 텍스트: OOS 적 · OOC 황 · IN_CONTROL 녹
export const judgementClass = (j) =>
  j === 'OOS' ? 'text-red' : j === 'OOC' ? 'text-amber' : 'text-green'

// 주체도 tint로 통일해 감사 화면에서 여러 색이 동시에 튀지 않게 한다.
export const actorVariant = (ac) => (ac === 'AGENT' ? 't-navy' : ac === 'HUMAN' ? 't-red' : 't-gray')

// 심각도 텍스트: HIGH 적 · MEDIUM 황 · LOW 회색
export const severityClass = (sev) =>
  sev === 'HIGH' ? 'text-red' : sev === 'MEDIUM' ? 'text-amber' : 'text-g1'

// 승인 상태 텍스트: API enum 전체를 빠짐없이 표시한다.
const APPROVAL_LABELS = {
  AUTO: '자동',
  PENDING: '승인 대기',
  APPROVED: '승인됨',
  REJECTED: '반려됨',
  EXPIRED: '만료됨',
}
export const approvalLabel = (status) => APPROVAL_LABELS[status] ?? status ?? '—'
export const approvalClass = (status) =>
  status === 'PENDING'
    ? 'text-red'
    : status === 'APPROVED'
      ? 'text-green'
      : status === 'REJECTED' || status === 'EXPIRED'
        ? 'text-red'
        : 'text-g1'

// .tbl 대응 공통 클래스 (fdc.css)
export const TH_CLS = 'whitespace-nowrap border-b border-line px-3 py-2.5 text-left text-[11px] font-semibold text-g1'
export const TD_CLS = 'whitespace-nowrap border-b border-cell-line px-3 py-[13px] align-middle text-[12.5px]'
export const CELL_ID = 'font-mono font-bold text-navy'
export const CELL_MONO = 'font-mono'
export const CELL_DIM = 'font-mono text-g1'
export const CELL_SUB = 'mt-[3px] font-mono text-[10.5px] text-g2'

// 행 변형: 짝수행 alt / 승인대기 row-red / 선택 row-sel
export const rowClass = (i, { red = false, sel = false } = {}) =>
  sel
    ? 'bg-row-sel shadow-[inset_0_0_0_1.5px_#2062A8]'
    : red
      ? 'bg-row-red shadow-[inset_0_0_0_1px_#E3BAC3]'
      : i % 2 === 1
        ? 'bg-alt'
        : ''
