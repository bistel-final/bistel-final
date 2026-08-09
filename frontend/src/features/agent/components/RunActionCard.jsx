import StatusBadge from '../../../shared/components/StatusBadge.jsx'
import { fmtDateTime } from '../../../shared/api/format.js'

const DASH = '—'

const CODE_TONE = { EQP_HOLD: 'navy', LOT_HOLD: 'blue', MONITOR: 'muted' }
const SEVERITY_TONE = { HIGH: 'oos', MEDIUM: 'ooc', LOW: 'neutral' }
const APPROVAL_LABEL = { AUTO: '자동 승인', PENDING: '승인 대기', APPROVED: '승인 완료', REJECTED: '반려' }
const APPROVAL_TONE = { AUTO: 'info', PENDING: 'ooc', APPROVED: 'ok', REJECTED: 'oos' }
const SEND_LABEL = { WAITING: '전송 대기', SENDING: '전송 중', SENT: '전송 완료', FAILED: '전송 실패' }
const SEND_TONE = { WAITING: 'neutral', SENDING: 'blue', SENT: 'ok', FAILED: 'oos' }

// 도메인 규칙 4 기본 결정표 — 조치 코드가 심각도·승인 방식·채널을 결정한다 (임의 지정 아님)
const DECISION_RULE = {
  EQP_HOLD: 'EQP_HOLD → HITL 승인 · HIGH · MES',
  LOT_HOLD: 'LOT_HOLD → 자동 승인 · MEDIUM · MES',
  MONITOR: 'MONITOR → 자동 승인 · LOW · EMAIL',
}

const RULE_TONE = { R01_OOS: 'oos', R02_OOC: 'ooc', R03_CONSEC: 'critical' }

function Row({ label, children }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-[62px] flex-none text-[11.5px] font-bold text-slate-light">{label}</span>
      {children}
    </div>
  )
}

// 권고 조치 카드 — run.action_id 로 조회한 조치를 그대로 보여준다
function RunActionCard({ action, rules }) {
  if (!action) {
    return (
      <div className="rounded-xl border border-line bg-white px-[18px] py-4 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
        <div className="mb-2 text-sm font-extrabold text-navy">권고 조치</div>
        <div className="text-[13px] font-semibold text-slate">조치 정보 실측 미제공</div>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-line bg-white px-[18px] py-4 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-sm font-extrabold text-navy">권고 조치</span>
        <StatusBadge tone="info" mono>
          decide_action
        </StatusBadge>
        <span className="ml-auto font-mono text-[12px] font-bold text-slate">{action.action_id}</span>
      </div>

      <div className="flex flex-col gap-2.5">
        <Row label="조치 코드">
          <StatusBadge tone={CODE_TONE[action.action_code] ?? 'neutral'} mono className="text-[13px] px-2.5 py-1">
            {action.action_code}
          </StatusBadge>
          <StatusBadge tone={SEVERITY_TONE[action.severity] ?? 'neutral'} mono>
            {action.severity}
          </StatusBadge>
        </Row>

        <Row label="승인·전송">
          <StatusBadge tone={APPROVAL_TONE[action.approval_status] ?? 'neutral'}>
            {APPROVAL_LABEL[action.approval_status] ?? action.approval_status}
          </StatusBadge>
          <StatusBadge tone={SEND_TONE[action.send_status] ?? 'neutral'}>
            {SEND_LABEL[action.send_status] ?? action.send_status}
          </StatusBadge>
          <span className="font-mono text-[11.5px] font-bold text-slate-light">{action.channel}</span>
        </Row>

        <Row label="생성 시각">
          {/* TODO(data): created_at 실측이 없는 조치는 "—" 로 둔다 (추정 생성 금지) */}
          <span className="font-mono text-[12.5px] font-bold text-ink">{fmtDateTime(action.created_at) || DASH}</span>
        </Row>

        <Row label="근거 룰">
          <span className="flex flex-wrap items-center gap-1.5">
            {rules.length === 0 && <span className="text-[12.5px] font-semibold text-slate">실측 미제공</span>}
            {rules.map((r) => (
              <StatusBadge key={r.rule_id} tone={RULE_TONE[r.rule_id] ?? 'neutral'} mono>
                {r.rule_id} {r.count}
              </StatusBadge>
            ))}
          </span>
        </Row>
      </div>

      <div className="mt-3 rounded-[10px] border border-line-soft bg-page px-3 py-2.5">
        <div className="text-[11.5px] font-bold text-slate-light">기본 결정표</div>
        <div className="mt-1 font-mono text-[12.5px] font-extrabold text-navy">
          {DECISION_RULE[action.action_code] ?? '결정표 실측 미제공'}
        </div>
      </div>
    </div>
  )
}

export default RunActionCard
