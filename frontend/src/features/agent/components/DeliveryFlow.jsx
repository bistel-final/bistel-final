import { fmtDateTime } from '../../../shared/api/format.js'
import Badge from '../../../shared/components/ui/Badge.jsx'
import { deliveryStatusMeta } from '../delivery-flow-state.js'
import { isNotificationAction, MES_MOCK_NOTICE } from '../notification-state.js'

const routeOf = (actionCode, approvalStatus, notification) => {
  if (actionCode === 'MONITORING') return ['내부 기록']
  if (actionCode === 'WARNING') return ['EMAIL', 'n8n', 'SMTP']
  if (notification) return ['n8n · 조치 알림', 'Kafka · 요청', 'MES Mock', 'Kafka · 모의 응답', 'n8n · 결과 반영']
  if (approvalStatus === 'REJECTED') return ['승인 요청', '승인 반려', 'Kafka 미발행']
  return ['승인 요청 EMAIL', '사람 승인', 'Kafka', 'MES Mock', 'write-back']
}

function DeliveryFlow({ action, compact = false }) {
  if (!action) return <div className="text-[12px] text-g2">조치가 아직 생성되지 않았습니다.</div>
  const deliveries = action.deliveries ?? []
  const notification = isNotificationAction(action)
  const route = routeOf(action.action_code, action.approval_status, notification)

  return (
    <div className="flex flex-col gap-3" data-testid="delivery-flow">
      <div className="text-[11px] text-g2">연동 경로 안내 · 실제 진행 상태는 아래 전달 기록 기준입니다.</div>
      <div className="flex flex-wrap items-center gap-1.5">
        {route.map((step, index) => (
          <span key={step} className="inline-flex items-center gap-1.5">
            <span className="rounded-md border border-line bg-soft px-2.5 py-1 font-mono text-[10.5px] font-bold text-g1">
              {step}
            </span>
            {index < route.length - 1 && <span className="text-[12px] font-bold text-blue">→</span>}
          </span>
        ))}
      </div>
      {action.action_code === 'MONITORING' && deliveries.length === 0 ? (
        <div className="text-[11.5px] font-semibold text-green-dark">외부 전송 없이 내부 기록으로 정상 종료합니다.</div>
      ) : deliveries.length === 0 ? (
        <div className="text-[11.5px] text-g2">전달 상태가 아직 기록되지 않았습니다.</div>
      ) : (
        <div className={`grid gap-2 ${compact ? 'grid-cols-1' : 'grid-cols-[repeat(auto-fit,minmax(180px,1fr))]'}`}>
          {deliveries.map((delivery) => {
            const meta = deliveryStatusMeta(delivery.status, delivery.channel)
            return (
              <div key={delivery.channel} className="rounded-lg border border-line bg-white px-3 py-2.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[11px] font-extrabold text-ink">{delivery.channel === 'MES' ? 'MES Mock' : delivery.channel}</span>
                  <Badge variant={meta.variant}>{meta.label}</Badge>
                </div>
                <div className="mt-2 space-y-1 font-mono text-[10px] text-g2">
                  <div>시작 {delivery.started_at ? fmtDateTime(delivery.started_at) : '미기록'}</div>
                  <div>완료 {delivery.completed_at ? fmtDateTime(delivery.completed_at) : '미기록'}</div>
                </div>
              </div>
            )
          })}
        </div>
      )}
      {action.action_code === 'EQP_HOLD' && <p className="text-[11.5px] text-g2">{MES_MOCK_NOTICE}</p>}
      {notification && action.action_code !== 'MONITORING' && <p className="text-[11.5px] text-g2">사용자 승인 없이 자동 연동합니다. 이메일은 발송 결과만 기록하며 사용자 열람·확인 여부는 수집하지 않습니다.</p>}
      {!notification && action.action_code === 'EQP_HOLD' && action.approval_status === 'PENDING' && (
        <div className="text-[11.5px] font-semibold text-tint-amber-text">승인 전 Kafka 미발행은 정상 대기 상태입니다.</div>
      )}
      {!notification && action.action_code === 'EQP_HOLD' && action.approval_status === 'REJECTED' && (
        <div className="text-[11.5px] font-semibold text-g1">승인 반려로 Kafka·MES 전송 없이 정상 종료했습니다.</div>
      )}
    </div>
  )
}

export default DeliveryFlow
