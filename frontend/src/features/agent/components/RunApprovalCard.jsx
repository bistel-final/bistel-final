import { useState } from 'react'
import { decideApproval } from '../../../shared/api/agent.js'
import Badge from '../../../shared/components/ui/Badge.jsx'
import Button from '../../../shared/components/ui/Button.jsx'

// 요청 body 의 decision 값은 APPROVE / REJECT — 응답·조회 상태값(APPROVED / REJECTED)과 다르다
const DECISION = { APPROVE: 'APPROVE', REJECT: 'REJECT' }
const RESULT_OF = { APPROVE: 'APPROVED', REJECT: 'REJECTED' }
const DECIDED_LABEL = { APPROVED: '승인 완료', REJECTED: '반려' }
const STATUS_VARIANT = { PENDING: 't-amber', APPROVED: 't-green', REJECTED: 't-red' }

// 이미 처리된 건의 재결정은 서버에서 409로 막힌다 — 화면에서도 같은 사유를 보여준다
const conflictMessage = (status) =>
  `이미 ${DECIDED_LABEL[status] ?? status} 처리된 승인 건입니다. 재결정할 수 없습니다.`

const inputCls =
  'h-9 w-full rounded-lg border border-line bg-soft px-3 text-[13px] text-ink disabled:cursor-not-allowed disabled:text-g2'

// 승인 섹션 — 결정자·코멘트 입력 후 승인/반려. 기존 동작 유지(decideApproval · 409 토스트 · AUTO 안내)
function RunApprovalCard({ action, approval, decided, onDecided, onToast }) {
  const [decidedBy, setDecidedBy] = useState('')
  const [decisionComment, setDecisionComment] = useState('')
  const [sending, setSending] = useState(false)

  if (!action) {
    return (
      <div>
        <div className="mb-2 text-xs font-bold text-g1">승인</div>
        <div className="rounded-lg border border-line bg-soft p-4 text-xs leading-[1.6] text-g1">
          Agent가 조치를 결정한 뒤 승인 필요 여부가 표시됩니다.
        </div>
      </div>
    )
  }

  // 자동 조치(LOT_HOLD·MONITOR)는 HITL 승인 대상이 아니다 — 폼 대신 안내만 노출
  if (action && action.approval_status === 'AUTO') {
    return (
      <div>
        <div className="mb-2 text-xs font-bold text-g1">승인</div>
        <div className="rounded-lg border border-line bg-soft p-4">
          <div className="flex items-center gap-2.5">
            <Badge variant="t-blue">AUTO</Badge>
            <span className="text-[13px] font-extrabold text-navy">자동 조치 — 승인 불필요</span>
          </div>
          <div className="mt-2 text-xs leading-[1.6] text-g1">
            {action.action_code} 는 기본 결정표의 자동 승인 조치입니다. 승인 절차 없이{' '}
            <span className="font-mono font-bold text-navy">{action.send_channel}</span> 채널로 전송됩니다.
          </div>
        </div>
      </div>
    )
  }

  const status = decided?.status ?? action?.approval_status ?? null
  // PENDING 이지만 승인 요청 레코드가 없으면 결정 API를 호출할 대상이 없다
  const missingApproval = status === 'PENDING' && !approval?.approval_id
  const locked = status !== 'PENDING' || Boolean(decided) || missingApproval
  const lockReason = decided
    ? `이 화면에서 ${DECIDED_LABEL[decided.status] ?? decided.status} 처리했습니다. 재결정은 서버에서 409로 거부됩니다.`
    : missingApproval
      ? '승인 요청 레코드 미확보 — 결정 대상 approval_id가 없습니다.'
      : conflictMessage(status)

  const notifyLocked = () =>
    onToast({
      tone: missingApproval ? 'ooc' : 'oos',
      title: missingApproval ? 'APPROVAL NOT FOUND' : '409 CONFLICT',
      message: lockReason,
    })

  const submit = (decision) => {
    if (locked) {
      notifyLocked()
      return
    }
    if (!decidedBy.trim()) {
      onToast({ tone: 'ooc', title: 'VALIDATION', message: '결정자를 입력해 주세요.' })
      return
    }
    setSending(true)
    decideApproval(approval.approval_id, {
      decision,
      decided_by: decidedBy.trim(),
      decision_comment: decisionComment.trim(),
    })
      .then((res) => {
        setSending(false)
        onDecided({
<<<<<<< Updated upstream
          status: res?.approval_status ?? RESULT_OF[decision],
=======
          status: res?.approval_status ?? (decision === 'APPROVE' ? 'APPROVED' : 'REJECTED'),
>>>>>>> Stashed changes
          decided_by: res?.decided_by ?? decidedBy.trim(),
          decision_comment: res?.decision_comment ?? decisionComment.trim(),
          approval_id: res?.approval_id ?? approval.approval_id,
          send_status: res?.send_status ?? null,
          agent_run_status: res?.agent_run_status ?? null,
        })
        onToast({
<<<<<<< Updated upstream
          tone: decision === DECISION.APPROVE ? 'ok' : 'oos',
          title: `APPROVAL ${decision}`,
          message: `${approval.approval_id} 를 ${DECIDED_LABEL[RESULT_OF[decision]]} 처리했습니다.`,
=======
          tone: decision === 'APPROVE' ? 'ok' : 'oos',
          title: `APPROVAL ${decision}`,
          message: `${approval.approval_id} 를 ${decision === 'APPROVE' ? '승인 완료' : '반려'} 처리했습니다.`,
>>>>>>> Stashed changes
        })
      })
      .catch((e) => {
        setSending(false)
        onToast({ tone: 'oos', title: 'REQUEST FAILED', message: e.message })
      })
  }

  return (
    <div>
      <div className="mb-2 flex items-center gap-2 text-xs font-bold text-g1">
        승인
        {status && <Badge variant={STATUS_VARIANT[status] ?? 't-gray'}>{status}</Badge>}
        {approval?.approval_id && <span className="ml-auto font-mono text-[11px] font-bold text-g2">{approval.approval_id}</span>}
      </div>
      <div className="flex flex-col gap-3 rounded-lg border border-line p-4">
        <label className="block">
          <span className="mb-1.5 block text-[11px] text-g1">결정자</span>
          <input
            type="text"
            value={decided?.decided_by ?? decidedBy}
            disabled={locked || sending}
            onChange={(e) => setDecidedBy(e.target.value)}
            placeholder="사번 또는 이름"
            className={`${inputCls} font-mono`}
          />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-[11px] text-g1">코멘트</span>
          <input
            type="text"
            value={decided?.decision_comment ?? decisionComment}
            disabled={locked || sending}
            onChange={(e) => setDecisionComment(e.target.value)}
            placeholder="결정 근거 (선택)"
            className={inputCls}
          />
        </label>
        {/* 잠긴 상태에서도 클릭하면 409 사유를 토스트로 알린다 — disabled 로 클릭을 삼키지 않는다 */}
        <div className="mt-1 flex gap-2.5">
          <Button
            variant="primary"
            aria-disabled={locked || sending}
<<<<<<< Updated upstream
            onClick={() => submit(DECISION.APPROVE)}
=======
            onClick={() => submit('APPROVE')}
>>>>>>> Stashed changes
            className={`flex-1 ${locked || sending ? 'cursor-not-allowed opacity-50' : ''}`}
          >
            {sending ? '전송 중…' : '승인'}
          </Button>
          <Button
            variant="outline-red"
            aria-disabled={locked || sending}
<<<<<<< Updated upstream
            onClick={() => submit(DECISION.REJECT)}
=======
            onClick={() => submit('REJECT')}
>>>>>>> Stashed changes
            className={`flex-1 ${locked || sending ? 'cursor-not-allowed opacity-50' : ''}`}
          >
            반려
          </Button>
        </div>
        {locked && (
          <div className="rounded-lg border border-tint-red-line bg-row-red px-3 py-2 text-[11.5px] leading-[1.5] text-red">
            {decided ? `${DECIDED_LABEL[decided.status] ?? decided.status} 처리됨` : '결정 불가'} — {lockReason}
          </div>
        )}
      </div>
    </div>
  )
}

export default RunApprovalCard
