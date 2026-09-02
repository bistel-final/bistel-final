export const DELIVERY_STATUS = Object.freeze({
  BLOCKED: { label: '차단됨', variant: 't-amber' },
  WAITING: { label: '전송 대기', variant: 't-blue' },
  SENDING: { label: '전송 중', variant: 'bg-blue' },
  SENT: { label: '전송 완료', variant: 't-green' },
  FAILED: { label: '전송 실패', variant: 't-red' },
  CANCELED: { label: '전송 취소', variant: 't-gray' },
  UNKNOWN: { label: '상태 미확인', variant: 'bg-gray' },
})

export const deliveryStatusMeta = (status) =>
  DELIVERY_STATUS[status] ?? DELIVERY_STATUS.UNKNOWN
