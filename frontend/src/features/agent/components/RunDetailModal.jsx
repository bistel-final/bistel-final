import { useState } from 'react'
import { fmtDateTime } from '../../../shared/api/format.js'
import Badge from '../../../shared/components/ui/Badge.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { actionCodeVariant } from '../../../shared/components/ui/statusStyles.js'
import { approvalText } from './agentModel.js'

// 근거 · 조치 상세 모달 — 라이트 시안 3-1 (920px, max-h 90vh, 백드롭 클릭 닫힘)
// 탭 3개: RAG 문서 근거 / 그래프 근거 (Neo4j) / 권고 조치 · 승인
const TABS = [
  { key: 'rag', label: 'RAG 문서 근거' },
  { key: 'graph', label: '그래프 근거 (Neo4j)' },
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

// 그래프 노드 색 — Chamber blue / Parameter sky / Alarm red / Fault amber / Action green / Document gray
const NODE_HEX = {
  Chamber: '#2563eb',
  Parameter: '#0ea5e9',
  Alarm: '#dc2626',
  Fault: '#f59e0b',
  Action: '#16a34a',
  Document: '#94a3b8',
}

function GraphDiagram({ run, docId }) {
  const nodes = [
    { type: 'Chamber', label: run.incident?.chamber_id, x: 120, y: 150 },
    { type: 'Parameter', label: run.sensor_id, x: 330, y: 78 },
    { type: 'Alarm', label: run.representative_alarm_id, x: 330, y: 222 },
    { type: 'Fault', label: run.fault_code, x: 560, y: 78 },
    { type: 'Action', label: run.recommended_action, x: 560, y: 222 },
    { type: 'Document', label: docId ?? 'Document', x: 760, y: 150 },
  ]
  const at = (t) => nodes.find((n) => n.type === t)
  const edges = [
    ['Alarm', 'Parameter', 'ON_PARAM'],
    ['Parameter', 'Chamber', 'MEASURED_ON'],
    ['Alarm', 'Chamber', 'OCCURRED_ON'],
    ['Alarm', 'Fault', 'CLASSIFIED_AS'],
    ['Fault', 'Action', 'RECOMMENDS'],
    ['Fault', 'Document', 'EVIDENCED_BY'],
  ]
  return (
    <svg viewBox="0 0 880 300" className="block w-full" fontFamily="IBM Plex Mono, monospace">
      {edges.map(([a, b, label]) => {
        const s = at(a)
        const t = at(b)
        const mx = (s.x + t.x) / 2
        const my = (s.y + t.y) / 2
        return (
          <g key={label}>
            <line x1={s.x} y1={s.y} x2={t.x} y2={t.y} stroke="var(--color-dash-line)" strokeWidth="1.4" />
            <text x={mx} y={my - 6} fontSize="8.5" fill="var(--color-g2)" textAnchor="middle">
              {label}
            </text>
          </g>
        )
      })}
      {nodes.map((n) => (
        <g key={n.type}>
          <circle cx={n.x} cy={n.y} r="24" fill={NODE_HEX[n.type]} opacity="0.14" />
          <circle cx={n.x} cy={n.y} r="24" fill="none" stroke={NODE_HEX[n.type]} strokeWidth="2" />
          <text x={n.x} y={n.y + 3.5} fontSize="9" fontWeight="700" fill={NODE_HEX[n.type]} textAnchor="middle">
            {n.type}
          </text>
          <text x={n.x} y={n.y + 42} fontSize="9.5" fontWeight="700" fill="var(--color-ink)" textAnchor="middle">
            {n.label}
          </text>
        </g>
      ))}
    </svg>
  )
}

function RunDetailModal({ open, onClose, run, action, approval, docs, decided, onDecide, deciding }) {
  const [tab, setTab] = useState('rag')
  if (!open) return null

  const hits = docs?.hits ?? []
  const docId = hits[0]?.document_id
  const cypher = `MATCH (a:Alarm {alarm_id: '${run.representative_alarm_id}'})-[:ON_PARAM]->(p:Parameter)-[:MEASURED_ON]->(c:Chamber)
MATCH (a)-[:CLASSIFIED_AS]->(f:Fault)-[:RECOMMENDS]->(act:Action)
OPTIONAL MATCH (f)-[:EVIDENCED_BY]->(d:Document)
RETURN a, p, c, f, act, d`
  const paths = [
    `(${run.representative_alarm_id})-[:ON_PARAM]->(${run.sensor_id})-[:MEASURED_ON]->(${run.incident?.chamber_id})`,
    `(${run.representative_alarm_id})-[:CLASSIFIED_AS]->(${run.fault_code})-[:RECOMMENDS]->(${run.recommended_action})`,
    ...(docId ? [`(${run.fault_code})-[:EVIDENCED_BY]->(${docId})`] : []),
  ]

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
          {tab === 'rag' &&
            (hits.length === 0 ? (
              <div className="rounded-[10px] border-[1.5px] border-dashed border-dash-line p-8 text-center text-[12.5px] text-g2">
                이 실행과 연결된 근거 문서가 없습니다
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {hits.slice(0, 3).map((h) => (
                  <div key={h.chunk_id} className="rounded-[10px] border border-line p-4">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-mono text-[11.5px] font-bold text-blue">{h.document_id}</span>
                      <span className="flex items-center gap-2">
                        <span className="h-[6px] w-[70px] overflow-hidden rounded-full bg-cell-line">
                          <span className="block h-full rounded-full bg-blue" style={{ width: `${Math.round(h.score * 100)}%` }} />
                        </span>
                        <span className="font-mono text-[11px] font-bold text-ink">{h.score.toFixed(2)}</span>
                      </span>
                    </div>
                    <div className="mt-1.5 text-[12.5px] font-bold text-ink">{h.section}</div>
                    {h.content && <div className="mt-1.5 text-[12px] leading-[1.65] text-g1">{h.content}</div>}
                  </div>
                ))}
              </div>
            ))}

          {tab === 'graph' && (
            <div className="flex flex-col gap-4">
              <pre className="overflow-x-auto rounded-[10px] bg-[#0f172a] px-4 py-3.5 font-mono text-[11.5px] leading-[1.7] text-[#dbeafe]">
                {cypher}
              </pre>
              <div className="rounded-[10px] border border-line p-3">
                <GraphDiagram run={run} docId={docId} />
              </div>
              <div>
                <div className="mb-1.5 text-[11px] font-bold text-g2">조회된 경로</div>
                <div className="flex flex-col gap-1 font-mono text-[11px] text-g1">
                  {paths.map((p) => (
                    <div key={p}>{p}</div>
                  ))}
                </div>
              </div>
            </div>
          )}

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
