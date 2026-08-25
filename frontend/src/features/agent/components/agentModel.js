// Agent 화면 상수·순수 함수 — 컴포넌트 파일(fast refresh 제약) 밖으로 분리한 모듈
// Fault 뱃지 스타일 — 라이트 시안 고정 매핑 (RFM·CDX red / MFD amber / TMD sky / FOC violet / OTH gray)
export const FAULT_BADGE_CLS = {
  RFM: 'bg-tint-red border-tint-red-line text-red',
  CDX: 'bg-tint-red border-tint-red-line text-red',
  MFD: 'bg-tint-amber border-tint-amber-line text-tint-amber-text',
  TMD: 'bg-[#e0f2fe] border-[#bae6fd] text-[#0369a1]',
  FOC: 'bg-[#ede9fe] border-[#ddd6fe] text-[#6d28d9]',
  OTH: 'bg-tint-gray border-tint-gray-line text-g1',
}

// 승인 상태 텍스트 — 시안: 승인 대기 amber / 승인됨 green / 반려됨 red / 자동 기록 green
export const approvalText = (status) =>
  status === 'PENDING'
    ? { label: '승인 대기', cls: 'text-amber-dark' }
    : status === 'APPROVED'
      ? { label: '승인됨', cls: 'text-green-dark' }
      : status === 'REJECTED'
        ? { label: '반려됨', cls: 'text-red' }
        : status === 'EXPIRED'
          ? { label: '만료됨', cls: 'text-red' }
          : { label: '자동 기록', cls: 'text-green-dark' }
