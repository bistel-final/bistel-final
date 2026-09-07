export const MOCK_NOTIFY_POLICY = 'MOCK-NOTIFY-V1'
export const isNotificationAction = (action) => action?.delivery_policy === MOCK_NOTIFY_POLICY
export const MES_MOCK_NOTICE = 'MES Mock 수신·모의 응답을 확인하는 연동입니다. 실제 설비 정지·LOT 배출·재가동은 검증하지 않습니다.'

export const deliveryFlowEdges = (edges, action) => !isNotificationAction(action) ? edges : edges
  .filter((edge) => edge.source !== 'approval' && edge.target !== 'approval')
  .map((edge) => edge.id === 'action-delivery' ? { ...edge, label: '자동 알림 · 모의 연동' } : edge)
