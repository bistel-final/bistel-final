import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { getActions } from '../../../shared/api/agent.js'
import { fmtDateTime } from '../../../shared/api/format.js'
import { ACTION_TABS } from '../mock/actions.js'
import ActionDetailPanel from '../components/ActionDetailPanel.jsx'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import StatusBadge from '../../../shared/components/StatusBadge.jsx'

// 값이 없으면 창작하지 않고 "—" 로 표기한다 (규칙: 데이터 창작 금지)
const DASH = '—'

// 헤더와 행이 같은 트랙을 쓰도록 그리드 템플릿을 한 곳에서 관리한다
const COLS =
  'grid-cols-[96px_168px_100px_106px_78px_182px_92px_84px_124px_78px] min-w-[1140px]'

// 탭 판정 — 승인 대기만 approval_status, 나머지는 send_status 기준 (명세)
const matchTab = (a, key) =>
  key === 'ALL' ? true : key === 'PENDING' ? a.approval_status === 'PENDING' : a.send_status === key

// 조치 코드 강조 단계: EQP_HOLD(설비 홀드) 최강 → LOT_HOLD 중간 → MONITOR 약하게
const CODE_TONE = { EQP_HOLD: 'navy', LOT_HOLD: 'blue', MONITOR: 'muted' }
const SEVERITY_TONE = { HIGH: 'oos', MEDIUM: 'ooc', LOW: 'neutral' }
const APPROVAL_LABEL = { AUTO: '자동', PENDING: '승인 대기', APPROVED: '승인 완료' }
const APPROVAL_TONE = { AUTO: 'info', PENDING: 'ooc', APPROVED: 'ok' }
const SEND_LABEL = { WAITING: '전송 대기', SENDING: '전송 중', SENT: '전송 완료', FAILED: '전송 실패' }
const SEND_TONE = { WAITING: 'neutral', SENDING: 'blue', SENT: 'ok', FAILED: 'oos' }

const HEADERS = [
  '조치 ID',
  'incident',
  '파라미터',
  '조치 코드',
  '심각도',
  '승인',
  '전송',
  '알람',
  '시각',
  '',
]

function ActionsPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [actions, setActions] = useState(null)
  const [error, setError] = useState(null)
  // 기본 선택은 '승인 대기'. 단, ?action=... 딥링크로 들어오면 해당 행이 어느 상태든
  // 보이도록 '전체'에서 시작한다 (초기 렌더에서 한 번만 평가)
  const [tab, setTab] = useState(() => (searchParams.get('action') ? 'ALL' : 'PENDING'))

  const load = useCallback(() => {
    getActions()
      .then((res) => setActions(res.items))
      .catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    load()
  }, [load])

  // 열린 행은 URL에 담는다 — 새로고침·링크 공유 시 그대로 복원된다
  const openId = searchParams.get('action')
  const toggleOpen = (id) => {
    const next = new URLSearchParams(searchParams)
    if (openId === id) next.delete('action')
    else next.set('action', id)
    setSearchParams(next, { replace: true })
  }

  const counts = useMemo(() => {
    const src = actions ?? []
    return Object.fromEntries(ACTION_TABS.map((t) => [t.key, src.filter((a) => matchTab(a, t.key)).length]))
  }, [actions])

  const rows = useMemo(() => (actions ?? []).filter((a) => matchTab(a, tab)), [actions, tab])

  if (error)
    return (
      <ErrorState
        title="조치 목록을 불러오지 못했습니다"
        detail={error}
        onRetry={() => {
          setError(null)
          setActions(null)
          load()
        }}
      />
    )
  if (!actions) return <LoadingState message="조치 목록을 불러오는 중…" />

  const alarmSum = rows.reduce((s, a) => s + (a.alarm_count ?? a.alarm_ids.length), 0)

  return (
    <div className="flex animate-[om-fadein_.3s_ease-out] flex-col gap-3.5">
      <div className="text-[21px] font-extrabold tracking-[-.3px] text-navy">조치 목록</div>

      {/* 상태 탭 — 건수는 응답 데이터에서 계산한다 (하드코딩 금지). 0건 탭도 회색으로 남긴다 */}
      <div className="flex flex-wrap items-center gap-1.5 rounded-xl border border-line bg-white px-[18px] py-3 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
        {ACTION_TABS.map((t) => {
          const on = tab === t.key
          const cnt = counts[t.key] ?? 0
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              className="flex cursor-pointer items-center gap-2 rounded-lg border px-[13px] py-[7px] text-[13.5px] font-bold"
              style={
                on
                  ? { background: '#1E5FC2', color: '#FFFFFF', borderColor: '#1E5FC2' }
                  : { background: '#FFFFFF', color: cnt === 0 ? '#94A3B8' : '#475569', borderColor: '#CBD5E1' }
              }
            >
              {t.label}
              <span
                className="rounded-md px-1.5 py-px font-mono text-[11.5px] font-extrabold"
                style={
                  on
                    ? { background: 'rgba(255,255,255,.22)', color: '#FFFFFF' }
                    : cnt === 0
                      ? { background: '#F1F5F9', color: '#94A3B8' }
                      : { background: '#EDF2FA', color: '#1E5FC2' }
                }
              >
                {cnt}
              </span>
            </button>
          )
        })}
        <span className="ml-auto text-[12.5px] font-bold text-slate">
          {rows.length}건 · 연관 알람 <span className="font-mono text-navy">{alarmSum}</span>건
        </span>
      </div>

      <div className="overflow-x-auto rounded-xl border border-line bg-white shadow-[0_1px_3px_rgba(15,42,92,.05)]">
        <div className={`grid ${COLS} gap-2 border-b border-line bg-page px-4 py-3 text-xs font-extrabold text-slate`}>
          {HEADERS.map((h, i) => (
            <span key={h || `col-${i}`}>{h}</span>
          ))}
        </div>

        {rows.length === 0 && (
          <EmptyState
            title="해당 상태의 조치가 없습니다"
            description="다른 상태 탭을 선택해 조회해 주세요."
          />
        )}

        {rows.map((a) => {
          const isOpen = openId === a.action_id
          const isPending = a.approval_status === 'PENDING'
          const alarmCount = a.alarm_count ?? a.alarm_ids.length
          return (
            <div key={a.action_id} className={`min-w-[1140px] ${isOpen ? 'bg-line-soft' : ''}`}>
              <div
                onClick={() => toggleOpen(a.action_id)}
                className={`grid ${COLS} cursor-pointer items-center gap-2 border-b border-line-soft px-4 py-2.5 transition-colors duration-[120ms] hover:bg-line-soft`}
                style={isOpen ? { boxShadow: 'inset 3px 0 0 #1E5FC2' } : undefined}
              >
                <span className="font-mono text-[12.5px] font-extrabold text-brand underline-offset-2 hover:underline">
                  {a.action_id}
                </span>

                <span className="flex flex-col leading-tight">
                  <span className="font-mono text-[12.5px] font-extrabold text-ink">{a.incident.lot_id}</span>
                  <span className="font-mono text-[11.5px] font-semibold text-slate-light">
                    {a.incident.chamber_id}
                  </span>
                </span>

                <span className="font-mono text-[12.5px] font-bold text-ink">{a.incident.sensor_id}</span>

                <span>
                  <StatusBadge tone={CODE_TONE[a.action_code] ?? 'neutral'} mono>
                    {a.action_code}
                  </StatusBadge>
                </span>

                <span>
                  <StatusBadge tone={SEVERITY_TONE[a.severity] ?? 'neutral'} mono>
                    {a.severity}
                  </StatusBadge>
                </span>

                {/* 승인과 전송은 서로 다른 축이므로 컬럼을 분리해 둔다 */}
                <span className="flex flex-col items-start gap-[3px]">
                  <StatusBadge tone={APPROVAL_TONE[a.approval_status] ?? 'neutral'}>
                    {APPROVAL_LABEL[a.approval_status] ?? a.approval_status}
                  </StatusBadge>
                  {a.approval_status === 'APPROVED' && (
                    <span className="font-mono text-[11px] font-semibold text-slate-light">
                      {a.approved_by || DASH} · {fmtDateTime(a.approved_at) || DASH}
                    </span>
                  )}
                </span>

                <span>
                  <StatusBadge tone={SEND_TONE[a.send_status] ?? 'neutral'}>
                    {a.send_status === 'SENDING' && (
                      <span className="mr-1 h-1.5 w-1.5 animate-[om-pulse_1.2s_ease-in-out_infinite] rounded-full bg-brand" />
                    )}
                    {SEND_LABEL[a.send_status] ?? a.send_status}
                  </StatusBadge>
                </span>

                {/* 알람 N건 → 해당 알람들만 필터된 알람 목록으로 이동 */}
                <span>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      navigate(`/alarms?alarms=${a.alarm_ids.join(',')}`)
                    }}
                    className="cursor-pointer rounded-md border border-line-input bg-white px-2 py-[3px] font-mono text-[11.5px] font-extrabold text-slate hover:border-brand hover:text-brand"
                  >
                    알람 {alarmCount}건
                  </button>
                </span>

                {/* TODO(data): action_history.csv 미확보로 created_at이 null인 7건이 있다.
                    알람 시각에서의 추정 생성은 값 창작이므로 하지 않고 "—"로 둔다 */}
                <span className="font-mono text-[12.5px] font-semibold text-ink">
                  {fmtDateTime(a.created_at) || DASH}
                </span>

                <span>
                  {isPending && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation()
                        navigate(`/agent-runs/${a.run_id}`)
                      }}
                      className="cursor-pointer rounded-lg border-none bg-navy px-2.5 py-[6px] font-sans text-[12px] font-bold text-white hover:bg-brand"
                    >
                      검토 →
                    </button>
                  )}
                </span>
              </div>

              {isOpen && (
                <div className="border-b border-line bg-page px-[18px] py-3.5">
                  <ActionDetailPanel actionId={a.action_id} />
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default ActionsPage
