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
          : { label: '승인 정보 없음', cls: 'text-g2' }

const RUN_STATUS_LABEL = Object.freeze({
  RUNNING: '분석 중',
  WAITING_APPROVAL: '승인 대기',
  COMPLETED: '분석 완료',
  FAILED: '분석 실패',
})

export const runStatusText = (status) => RUN_STATUS_LABEL[status] ?? status ?? '상태 미제공'

const DIAGNOSTIC_STATUS_LABEL = Object.freeze({
  AVAILABLE: '분석 완료',
  EMPTY: '정보 없음',
  SUFFICIENT: '근거 충분',
  PARTIAL: '근거 일부 부족',
  CONFLICT: '근거 충돌',
  NOT_AVAILABLE_STATIC_DATASET: '정적 데이터로 관찰 불가',
})

export const diagnosticStatusText = (status) =>
  DIAGNOSTIC_STATUS_LABEL[status] ?? status ?? '상태 미제공'

const DIAGNOSTIC_REASON_LABEL = Object.freeze({
  DIRECT_SCOPE_MISSING: '직접 영향 대상을 확정할 근거가 없습니다.',
  NOT_ENOUGH_RUNTIME_HISTORY: '비교할 과거 Agent 실행 이력이 부족합니다.',
})

export const diagnosticReasonText = (reason) =>
  DIAGNOSTIC_REASON_LABEL[reason] ?? (reason ? `사유 코드 ${reason}` : '상세 사유 미제공')

const TOOL_STATUS_LABEL = Object.freeze({
  SUCCESS: '수집 완료',
  ERROR: '수집 실패',
})

export const toolStatusText = (status) => TOOL_STATUS_LABEL[status] ?? status ?? '상태 미제공'

const IMPACT_KIND_LABEL = Object.freeze({
  LOT: '대상 LOT',
  WAFER: '발생 WAFER',
  CHAMBER: '발생 챔버',
  PARAMETER: '관련 파라미터',
  PROCESS_STEP: '영향 공정',
  SIBLING_CHAMBER: '연관 챔버',
})

const IMPACT_RELATION_LABEL = Object.freeze({
  UPSTREAM: '상류 공정',
  DOWNSTREAM: '하류 공정',
  RELATED: '연관 파라미터',
})

export const impactLabelOf = (item) =>
  IMPACT_RELATION_LABEL[item?.relation] ?? IMPACT_KIND_LABEL[item?.kind] ?? '영향 대상'

export const impactSourceOf = (item) => {
  const sourceId = String(item?.source_id ?? '').split(':').at(-1)
  if (item?.kind !== 'WAFER') return sourceId
  const waferNumber = sourceId.match(/W0*(\d+)$/)?.[1]
  return waferNumber ? `W${Number(waferNumber)}` : sourceId
}

const APPROVAL_STATUS_LABEL = Object.freeze({
  PENDING: '승인 대기',
  APPROVED: '승인 완료',
  REJECTED: '승인 반려',
  EXPIRED: '승인 기한 만료',
})

export const approvalStatusSummary = (action, approval) => {
  if (!action) return '조치 미생성 · 승인 요청 없음'
  if (action.action_code !== 'EQP_HOLD') return '승인 불필요 · 자동 전달 정책'
  const status = approval?.status ?? action.approval_status
  return APPROVAL_STATUS_LABEL[status] ?? '승인 상태 확인 필요'
}

const DELIVERY_CHANNEL_LABEL = Object.freeze({
  EMAIL: '이메일',
  KAFKA: 'Kafka',
  MES: 'MES',
})

const DELIVERY_STATUS_LABEL = Object.freeze({
  WAITING: '대기',
  SENDING: '전달 중',
  SENT: '전달 완료',
  FAILED: '전달 실패',
  SKIPPED: '전달 제외',
})

export const deliveryChannelText = (channel) => DELIVERY_CHANNEL_LABEL[channel] ?? channel
export const deliveryStatusText = (status) => DELIVERY_STATUS_LABEL[status] ?? status

export const deliveryStatusSummary = (action) => {
  if (!action) return '조치가 생성되지 않아 전달 없음'
  if (!action.deliveries?.length) return '전달 내역 없음'
  return action.deliveries
    .map((delivery) => `${deliveryChannelText(delivery.channel)} ${deliveryStatusText(delivery.status)}`)
    .join(' · ')
}
