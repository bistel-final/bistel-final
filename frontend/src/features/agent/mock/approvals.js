// dc.html 승인 큐 2건 + 초기 전송 결과 이력
export const APPROVALS = [
  {
    id: 'APR-0001',
    action_id: 'ACT-0005',
    alarm_id: 'ALM-0022',
    action_code: 'EQP_HOLD',
    meta: 'LOT-260007 · PHO-01 · EQP_HOLD · R03: ALM-0022',
    status: 'PENDING',
  },
  {
    id: 'APR-0002',
    action_id: 'ACT-0010',
    alarm_id: 'ALM-0048',
    action_code: 'EQP_HOLD',
    meta: 'LOT-260010 · ETC-01 · EQP_HOLD · R03: ALM-0048',
    status: 'PENDING',
  },
]

export const SEND_HISTORY = [{ k: 'SENT', label: 'ACT-0002 · ALM-0005 · EQP_HOLD' }]
