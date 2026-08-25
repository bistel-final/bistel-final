import { useState } from 'react'
import { fmtDateTime } from '../../../shared/api/format.js'
import Badge from '../../../shared/components/ui/Badge.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { actionCodeVariant } from '../../../shared/components/ui/statusStyles.js'
import { approvalText } from './agentModel.js'
import RunGraphEvidenceTab from './RunGraphEvidenceTab.jsx'
import RunRagEvidenceTab from './RunRagEvidenceTab.jsx'

// 근거 · 조치 상세 모달 — 라이트 시안 3-1 (920px, max-h 90vh, 백드롭 클릭 닫힘)
// 탭 3개: RAG 문서 근거 / 그래프 근거 / 권고 조치 · 승인
const TABS = [
  { key: 'rag', label: 'RAG 문서 근거' },
  { key: 'graph', label: '그래프 근거' },
  { key: 'act', label: '권고 조치 · 승인' },
]

// 조치 절차 체크리스트 — EQP_HOLD 4단계 / 그 외 3단계 (시안 고정 문안)
const CHECKLIST = {
  EQP_HOLD: [
    '설비 투입 중단 — MES에 EQP HOLD 등록',
    '진행 중 LOT 배출 확인',
    '담당 엔지니어 점검 배정',
    '점검 완료 후 HOLD 해제 · 재가동',
  ],
  LOT_HOLD: ['해당 LOT 진행 중단 등록', '후속 계측 의뢰 · 결과 확인', '이상 없으면 HOLD 해제'],
  MONITOR: ['해당 챔버 모니터링 강화 등록', '다음 LOT 처리 결과 확인', '재발 시 조치 상향 검토'],
}

function RunDetailModal({ open, onClose, run, action, approval, docs, decided, onDecide, deciding }) {
  const [tab, setTab] = useState('rag')
  if (!open) return null

  const hits = docs?.hits ?? []

  const status = decided ?? action?.approval_status
  const ap = approvalText(status)
  const isHold = action?.action_code === 'EQP_HOLD'
  const steps = CHECKLIST[action?.action_code] ?? CHECKLIST.MONITOR
  const mesSent = decided === 'APPROVED' || action?.send_status === 'SENT'

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
              {run.agent_run_id} · {run.fault_code} · {run.recommended_action}
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
          {tab === 'rag' && <RunRagEvidenceTab hits={hits} />}

          {tab === 'graph' && <RunGraphEvidenceTab run={run} />}

          {tab === 'act' && action && (
            <div className="flex flex-col gap-5">
              <div className="flex items-center gap-3">
                <Badge variant={actionCodeVariant(action.action_code)}>{action.action_code}</Badge>
                <span className="text-[12.5px] text-g1">{action.reason}</span>
              </div>

              <div>
                <div className="mb-2 text-[11px] font-bold text-g2">조치 절차</div>
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
                <div className="mb-1.5 text-[11px] font-bold text-g2">MES 전송 상태</div>
                <span className={`font-mono text-[12.5px] font-bold ${mesSent ? 'text-green-dark' : decided === 'REJECTED' ? 'text-red' : 'text-g1'}`}>
                  {decided === 'APPROVED'
                    ? 'SENT · 전송 완료'
                    : decided === 'REJECTED'
                      ? 'CANCELED · 전송 취소'
                      : `${action.send_status}${action.sent_at ? ` · ${fmtDateTime(action.sent_at)}` : ''} (${action.send_channel})`}
                </span>
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
                      <Button sm disabled={deciding} onClick={() => onDecide('APPROVE')}>
                        승인
                      </Button>
                      <Button sm variant="outline-red" disabled={deciding} onClick={() => onDecide('REJECT')}>
                        반려
                      </Button>
                    </div>
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
        </div>
      </div>
    </div>
  )
}

export default RunDetailModal
