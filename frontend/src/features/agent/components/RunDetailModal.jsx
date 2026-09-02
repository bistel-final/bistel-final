import { useState } from 'react'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import RunAuditSubview from '../../../shared/components/audit/RunAuditSubview.jsx'
import Badge from '../../../shared/components/ui/Badge.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { actionCodeVariant } from '../../../shared/components/ui/statusStyles.js'
import { approvalText } from './agentModel.js'
import DeliveryFlow from './DeliveryFlow.jsx'
import RunGraphEvidenceTab from './RunGraphEvidenceTab.jsx'
import RunRagEvidenceTab from './RunRagEvidenceTab.jsx'

// 근거 · 조치 상세 모달 — 라이트 시안 3-1 (920px, max-h 90vh, 백드롭 클릭 닫힘)
// 이전 시안의 상세 모달 안에서 C-5.2의 공개 근거·승인·감사 계약을 함께 제공한다.
const TABS = [
  { key: 'rag', label: 'RAG 문서 근거' },
  { key: 'graph', label: '그래프 근거' },
  { key: 'act', label: '권고 조치 · 승인' },
  { key: 'audit', label: '감사 이력' },
]

// 조치 절차 체크리스트 — EQP_HOLD 4단계 / 그 외 3단계 (시안 고정 문안)
const CHECKLIST = {
  EQP_HOLD: [
    '설비 투입 중단 — MES에 EQP HOLD 등록',
    '진행 중 LOT 배출 확인',
    '담당 엔지니어 점검 배정',
    '점검 완료 후 HOLD 해제 · 재가동',
  ],
  WARNING: ['이상 경고 이메일 발송', '다음 LOT 처리 결과 확인', '재발 시 조치 상향 검토'],
  MONITORING: ['해당 챔버 모니터링 강화 등록', '다음 LOT 처리 결과 확인', '재발 시 조치 상향 검토'],
}

function RunDetailModal({
  open,
  onClose,
  run,
  detail,
  action,
  approval,
  docs,
  evidenceItems = [],
  approvalState,
  onDecide,
}) {
  const [tab, setTab] = useState('rag')
  const [decidedBy, setDecidedBy] = useState('')
  const [comment, setComment] = useState('')
  if (!open) return null

  const hits = docs?.hits ?? []

  const status = approvalState?.status ?? approval?.status ?? action?.approval_status
  const ap = approvalText(status)
  const actionCode = action?.action_code ?? run.recommended_action
  const actionReason = action?.reason ?? '규칙 기반 조치 사유 미제공'
  const verificationSteps = detail?.diagnosis?.verification_steps ?? []
  const isHold = actionCode === 'EQP_HOLD'
  const steps = CHECKLIST[actionCode] ?? CHECKLIST.MONITORING
  const deciding = approvalState?.phase === 'pending'

  const tabCls = (on) =>
    `inline-flex h-8 cursor-pointer items-center rounded-lg border px-3.5 text-[12px] font-bold ${
      on ? 'border-blue bg-tint-blue text-blue-hover' : 'border-field-line bg-white text-g2'
    }`

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(15,23,42,.5)] p-6"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="flex max-h-[90vh] w-[920px] flex-col overflow-hidden rounded-xl bg-white shadow-[0_20px_60px_rgba(15,23,42,.3)]"
        onClick={(e) => e.stopPropagation()}
        role="presentation"
      >
        <div className="flex items-center justify-between border-b border-line px-6 py-4">
          <div>
            <div className="text-[15px] font-extrabold text-ink">근거 · 조치 상세</div>
            <div className="mt-0.5 font-mono text-[11px] text-g2">
              {run.agent_run_id} · {run.fault_code} · {actionCode}
            </div>
          </div>
          <button type="button" onClick={onClose} className="cursor-pointer text-[20px] leading-none text-g2 hover:text-ink">
            ×
          </button>
        </div>

        <div className="flex gap-2 border-b border-cell-line px-6 py-3">
          {TABS.map((t) => (
            <button key={t.key} type="button" className={tabCls(tab === t.key)} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </div>

        <div className="overflow-y-auto px-6 py-5">
          {tab === 'rag' && <RunRagEvidenceTab hits={hits} diagnosis={detail?.diagnosis} />}

          {tab === 'graph' && (
            <RunGraphEvidenceTab
              run={run}
              evidenceItems={evidenceItems}
              diagnosis={detail?.diagnosis}
            />
          )}

          {tab === 'act' && !action && <EmptyState title="아직 결정된 조치가 없습니다" />}

          {tab === 'act' && action && (
            <div className="flex flex-col gap-5">
              <div className="rounded-[10px] border border-tint-blue-line bg-tint-blue px-4 py-3.5">
                <div className="flex items-center gap-3">
                  <Badge variant={actionCodeVariant(actionCode)}>{actionCode}</Badge>
                  <span className="text-[12.5px] font-semibold text-ink">{actionReason}</span>
                </div>
                <div className="mt-3 border-t border-tint-blue-line pt-2.5 text-[11.5px] leading-6 text-g1">
                  <strong className="text-navy">다음 확인:</strong>{' '}
                  {verificationSteps.length > 0 ? verificationSteps.join(' → ') : '추가 확인 절차 미제공'}
                </div>
              </div>

              <div>
                <div className="mb-2 text-[11px] font-bold text-g2">조치 실행 절차</div>
                <div className="flex flex-col gap-2.5">
                  {steps.map((s, i) => (
                    <div key={s} className="flex items-center gap-3">
                      <span className="flex h-[22px] w-[22px] flex-none items-center justify-center rounded-full bg-tint-blue font-mono text-[10.5px] font-bold text-blue">
                        {i + 1}
                      </span>
                      <span className="text-[12.5px] text-ink">{s}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <div className="mb-1.5 text-[11px] font-bold text-g2">조치 전달 흐름</div>
                <DeliveryFlow action={action} />
              </div>

              {isHold ? (
                status === 'PENDING' ? (
                  <div className="rounded-[10px] border border-tint-red-line bg-row-red p-4">
                    <div className="text-[12.5px] font-bold text-red">EQP_HOLD 승인 대기 — 설비 정지는 사람이 최종 결정합니다</div>
                    <div className="mt-1 text-[11.5px] text-g1">
                      승인 시 MES로 HOLD 이벤트가 전송되고, 반려 시 전송이 취소됩니다.
                      {approval && <span className="ml-1 font-mono text-g2">({approval.approval_id})</span>}
                    </div>
                    <div className="mt-3 flex gap-2">
                      <input
                        value={decidedBy}
                        onChange={(event) => setDecidedBy(event.target.value)}
                        placeholder="결정자"
                        disabled={deciding}
                        className="h-8 min-w-0 flex-1 rounded-lg border border-field-line bg-white px-3 text-[12px]"
                      />
                      <input
                        value={comment}
                        onChange={(event) => setComment(event.target.value)}
                        placeholder="결정 근거 (선택)"
                        disabled={deciding}
                        className="h-8 min-w-0 flex-[2] rounded-lg border border-field-line bg-white px-3 text-[12px]"
                      />
                    </div>
                    <div className="mt-2 flex gap-2">
                      <Button sm disabled={deciding} onClick={() => onDecide('APPROVED', decidedBy, comment)}>
                        승인
                      </Button>
                      <Button sm variant="outline-red" disabled={deciding} onClick={() => onDecide('REJECTED', decidedBy, comment)}>
                        반려
                      </Button>
                    </div>
                    {approvalState?.error && <div className="mt-2 text-[11.5px] font-bold text-red">{approvalState.error}</div>}
                    {approvalState?.phase === 'conflict' && (
                      <div className="mt-2 text-[11.5px] font-bold text-tint-amber-text">
                        이미 처리되어 최신 상태를 다시 조회했습니다.
                      </div>
                    )}
                  </div>
                ) : (
                  <div className={`text-[13px] font-bold ${ap.cls}`}>{ap.label}</div>
                )
              ) : (
                <div className="rounded-[10px] border border-tint-green-line bg-state-green-bg px-4 py-3 text-[12px] text-green-dark">
                  자동 기록 — EQP_HOLD 외 조치는 승인 없이 감사로그에 기록됩니다
                </div>
              )}
            </div>
          )}

          {tab === 'audit' && (
            <RunAuditSubview
              agent_run_id={run.agent_run_id}
              action_id={action?.action_id}
              approval_id={approval?.approval_id}
            />
          )}
        </div>
      </div>
    </div>
  )
}

export default RunDetailModal
