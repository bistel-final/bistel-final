import { fmtDateTime } from '../../../shared/api/format.js'
import Badge from '../../../shared/components/ui/Badge.jsx'
import { deliveryStatusMeta } from '../delivery-flow-state.js'
import { isNotificationAction, MES_MOCK_NOTICE } from '../notification-state.js'

// n8n 워크플로 경로 — 각 단계는 시스템 이름과 workflow 역할을 함께 보여 준다.
// channel은 그 단계의 상태를 어느 전달 기록에서 읽을지 정한다(EMAIL 또는 MES).
const STAGE = (system, role, channel = null) => ({ system, role, channel })
const laneOf = (actionCode, approvalStatus, notification) => {
  if (actionCode === 'MONITORING') return [[STAGE('Agent', '내부 기록')]]
  if (actionCode === 'WARNING') return [[STAGE('Agent', '경고 알림 요청'), STAGE('n8n WF2', '이메일 알림', 'EMAIL'), STAGE('SMTP', '수신자 발송', 'EMAIL')]]
  if (notification) return [
    [STAGE('Agent', '조치 알림 요청'), STAGE('n8n WF2', '이메일 알림', 'EMAIL'), STAGE('SMTP', '수신자 발송', 'EMAIL')],
    [STAGE('Agent', 'HOLD 요청'), STAGE('n8n WF3', 'MES 요청 검증 · 발행', 'MES'), STAGE('Kafka', 'fdc.actions 요청', 'MES'), STAGE('MES Mock', '모의 처리', 'MES'), STAGE('Kafka', 'fdc.actions.result 모의 응답', 'MES'), STAGE('n8n WF4', '결과 반영 · 시스템 콜백', 'MES')],
  ]
  if (approvalStatus === 'REJECTED') return [[STAGE('Agent', '승인 요청'), STAGE('운영자', '승인 반려'), STAGE('Kafka', '미발행')]]
  return [[STAGE('Agent', '승인 요청 EMAIL', 'EMAIL'), STAGE('운영자', '사람 승인'), STAGE('n8n WF3', 'MES 요청 발행', 'MES'), STAGE('Kafka', 'fdc.actions', 'MES'), STAGE('MES Mock', '모의 처리', 'MES'), STAGE('n8n WF4', 'write-back', 'MES')]]
}

const stageTone = (status) => {
  if (status === 'SENT') return 'border-tint-green-line bg-state-green-bg text-green-dark'
  if (status === 'FAILED') return 'border-tint-red-line bg-row-red text-red'
  if (status === 'WAITING' || status === 'SENDING') return 'border-tint-amber-line bg-tint-amber text-tint-amber-text'
  return 'border-line bg-soft text-g1'
}

function DeliveryFlow({ action, compact = false }) {
  if (!action) return <div className="text-[12px] text-g2">조치가 아직 생성되지 않았습니다.</div>
  const deliveries = action.deliveries ?? []
  const notification = isNotificationAction(action)
  const lanes = laneOf(action.action_code, action.approval_status, notification)
  const statusOf = (channel) => deliveries.find((delivery) => delivery.channel === channel)?.status ?? null

  return (
    <div className="flex flex-col gap-3" data-testid="delivery-flow">
      <div className="text-[11px] text-g2">n8n 워크플로 연동 경로 · 단계 색은 전달 기록 상태(초록 완료 · 노랑 진행 · 빨강 실패)를 따릅니다.</div>
      <div className="flex flex-col gap-2">
        {lanes.map((lane, laneIndex) => (
          <div key={laneIndex} className="flex flex-wrap items-stretch gap-1.5">
            {lane.map((stage, index) => (
              <span key={`${stage.system}-${stage.role}`} className="inline-flex items-center gap-1.5">
                <span className={`flex min-w-[96px] flex-col rounded-lg border px-2.5 py-1.5 ${stageTone(stage.channel ? statusOf(stage.channel) : null)}`}>
                  <span className="font-mono text-[10.5px] font-extrabold">{stage.system}</span>
                  <span className="text-[10.5px] font-semibold">{stage.role}</span>
                </span>
                {index < lane.length - 1 && <span className="text-[13px] font-bold text-blue">→</span>}
              </span>
            ))}
          </div>
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
