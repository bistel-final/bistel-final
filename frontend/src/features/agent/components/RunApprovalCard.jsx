import { useState } from 'react'
import { decideApproval } from '../../../shared/api/agent.js'
import StatusBadge from '../../../shared/components/StatusBadge.jsx'

const DECIDED_LABEL = { APPROVED: '승인 완료', REJECTED: '반려' }

// 이미 처리된 건의 재결정은 서버에서 409로 막힌다 — 화면에서도 같은 사유를 보여준다
const conflictMessage = (status) =>
  `이미 ${DECIDED_LABEL[status] ?? status} 처리된 승인 건입니다. 재결정할 수 없습니다.`

function RunApprovalCard({ action, approval, decided, onDecided, onToast }) {
  const [decidedBy, setDecidedBy] = useState('')
  const [comment, setComment] = useState('')
  const [sending, setSending] = useState(false)

  // 자동 조치(LOT_HOLD·MONITOR)는 HITL 승인 대상이 아니다 — 폼 대신 안내만 노출
  if (action && action.approval_status === 'AUTO') {
    return (
      <div className="rounded-xl border border-line bg-white px-[18px] py-4 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
        <div className="mb-3 text-sm font-extrabold text-navy">승인</div>
        <div className="flex items-start gap-3 rounded-[10px] border border-[#C7DBF7] bg-line-soft px-3.5 py-3">
          <StatusBadge tone="info" mono>
            AUTO
          </StatusBadge>
          <div className="flex flex-col gap-1">
            <div className="text-[14px] font-extrabold text-brand">자동 조치 — 승인 불필요</div>
            <div className="text-[12.5px] font-semibold leading-[1.5] text-slate">
              {action.action_code} 는 기본 결정표에서 자동 승인으로 정해진 조치입니다. 승인 절차 없이{' '}
              <span className="font-mono font-bold text-navy">{action.channel}</span> 채널로 전송됩니다.
            </div>
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
      comment: comment.trim(),
    })
      .then((res) => {
        setSending(false)
        onDecided({
          status: res?.status ?? decision,
          decided_by: res?.decided_by ?? decidedBy.trim(),
          comment: res?.comment ?? comment.trim(),
          approval_id: res?.approval_id ?? approval.approval_id,
        })
        onToast({
          tone: decision === 'APPROVED' ? 'ok' : 'oos',
          title: `APPROVAL ${decision}`,
          message: `${approval.approval_id} 를 ${DECIDED_LABEL[decision]} 처리했습니다.`,
        })
      })
      .catch((e) => {
        setSending(false)
        onToast({ tone: 'oos', title: 'REQUEST FAILED', message: e.message })
      })
  }

  const inputCls =
    'w-full rounded-lg border border-line-input px-3 py-2 text-[13.5px] font-semibold text-navy placeholder:font-normal placeholder:text-[#94A3B8] disabled:cursor-not-allowed disabled:bg-page disabled:text-slate-light'

  return (
    <div className="rounded-xl border border-line bg-white px-[18px] py-4 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
      <div className="mb-3 flex items-center gap-2">
        <span className="text-sm font-extrabold text-navy">승인</span>
        <StatusBadge tone={status === 'PENDING' && !locked ? 'ooc' : 'neutral'} mono>
          {status ?? '—'}
        </StatusBadge>
        {approval?.approval_id && (
          <span className="ml-auto font-mono text-[12px] font-bold text-slate">{approval.approval_id}</span>
        )}
      </div>

      {locked && (
        <div
          onClick={notifyLocked}
          className="mb-3 cursor-pointer rounded-[10px] border border-[#FECACA] bg-oos-soft px-3.5 py-2.5 text-[12.5px] font-bold leading-[1.5] text-oos"
        >
          {decided ? `${DECIDED_LABEL[decided.status] ?? decided.status} 처리됨` : '결정 불가'} — {lockReason}
        </div>
      )}

      <div className="flex flex-col gap-2.5">
        <label className="flex flex-col gap-1.5">
          <span className="text-[11.5px] font-bold text-slate-light">결정자</span>
          <input
            type="text"
            value={decided?.decided_by ?? decidedBy}
            disabled={locked || sending}
            onChange={(e) => setDecidedBy(e.target.value)}
            placeholder="사번 또는 이름"
            className={`${inputCls} font-mono`}
          />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-[11.5px] font-bold text-slate-light">코멘트</span>
          <textarea
            rows={2}
            value={decided?.comment ?? comment}
            disabled={locked || sending}
            onChange={(e) => setComment(e.target.value)}
            placeholder="결정 근거를 남겨 주세요 (선택)"
            className={`${inputCls} resize-none leading-[1.5]`}
          />
        </label>
      </div>

      <div className="mt-3.5 flex items-center gap-2.5" onClick={locked ? notifyLocked : undefined}>
        <button
          type="button"
          aria-disabled={locked || sending}
          onClick={() => submit('APPROVED')}
          className={`flex-1 rounded-lg border-none px-4 py-2.5 text-[14px] font-extrabold text-white ${
            locked || sending ? 'cursor-not-allowed bg-[#94A3B8]' : 'cursor-pointer bg-brand hover:bg-brand-light'
          }`}
        >
          {sending ? '전송 중…' : '승인'}
        </button>
        <button
          type="button"
          aria-disabled={locked || sending}
          onClick={() => submit('REJECTED')}
          className={`flex-1 rounded-lg border bg-white px-4 py-2.5 text-[14px] font-extrabold ${
            locked || sending
              ? 'cursor-not-allowed border-line-input text-slate-light'
              : 'cursor-pointer border-oos text-oos hover:bg-oos-soft'
          }`}
        >
          반려
        </button>
      </div>

      {decided && (
        <div className="mt-3 rounded-[10px] border border-line bg-page px-3.5 py-2.5 text-[12.5px] font-semibold leading-[1.55] text-slate">
          결정 요청을 보냈습니다. run 상태·전송 상태는 서버 커밋 후 갱신됩니다.
          {/* TODO(api): 결정 후 run/action 상태 재조회(POST 응답에 갱신 상태 미포함) */}
        </div>
      )}
    </div>
  )
}

export default RunApprovalCard
