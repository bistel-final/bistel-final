import { useCallback, useEffect, useMemo, useState } from 'react'
import { getAuditLogs } from '../../../shared/api/analytics.js'
import { AUDIT_EVENT_TYPES } from '../mock/auditLogs.js'
import JsonDiff from '../../../shared/components/JsonDiff.jsx'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'

const PAGE_SIZE = 8

const evColor = (ev) =>
  ev.includes('FAILED')
    ? ['#FEE2E2', '#DC2626']
    : ev === 'ACTION_SENT' || ev.includes('COMPLETED')
      ? ['#DCFCE7', '#16A34A']
      : ev.includes('REQUESTED') || ev.includes('DECIDED')
        ? ['#FEF3C7', '#D97706']
        : ['#EDF2FA', '#1E5FC2']

const acColor = (k) =>
  k === 'HUMAN' ? ['#0F2A5C', '#FFFFFF'] : k === 'AGENT' ? ['#DBEAFE', '#1E5FC2'] : ['#F1F5F9', '#475569']

function AuditLogPage() {
  const [logs, setLogs] = useState(null)
  const [error, setError] = useState(null)
  const [events, setEvents] = useState([])
  const [actor, setActor] = useState('전체')
  const [from, setFrom] = useState('2026-06-01')
  const [to, setTo] = useState('2026-06-04')
  const [target, setTarget] = useState('')
  const [page, setPage] = useState(1)
  const [open, setOpen] = useState(null)

  const load = useCallback(() => {
    getAuditLogs()
      .then((data) => setLogs([...data].sort((a, b) => b.ts.localeCompare(a.ts))))
      .catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const retry = () => {
    setError(null)
    setLogs(null)
    load()
  }

  const filtered = useMemo(() => {
    if (!logs) return []
    return logs.filter(
      (e) =>
        (events.length === 0 || events.includes(e.ev)) &&
        (actor === '전체' || e.ac === actor) &&
        e.ts.slice(0, 10) >= from &&
        e.ts.slice(0, 10) <= to &&
        (!target || e.entId.toLowerCase().includes(target.toLowerCase())),
    )
  }, [logs, events, actor, from, to, target])

  if (error) return <ErrorState detail={error} onRetry={retry} />
  if (!logs) return <LoadingState message="감사로그를 불러오는 중…" />

  const pageCnt = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const curPage = Math.min(page, pageCnt)
  const rows = filtered.slice((curPage - 1) * PAGE_SIZE, curPage * PAGE_SIZE)
  const typeCnts = AUDIT_EVENT_TYPES.map((t) => ({ name: t, cnt: filtered.filter((e) => e.ev === t).length }))
  const typeMax = Math.max(1, ...typeCnts.map((t) => t.cnt))
  const todayCnt = logs.filter((e) => e.ts.startsWith('2026-06-04')).length
  const failedCnt = logs.filter((e) => e.ev.includes('FAILED')).length
  const resetPage = () => setPage(1)

  const toggleEvent = (t) => {
    setEvents((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]))
    resetPage()
  }

  return (
    <div className="flex animate-[om-fadein_.3s_ease-out] flex-col gap-3.5">
      <div className="text-[21px] font-extrabold tracking-[-.3px] text-navy">감사로그</div>
      <div className="flex flex-col gap-[11px] rounded-xl border border-line bg-white px-[18px] py-3.5 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
        <div className="flex flex-wrap items-center gap-[7px]">
          <span className="mr-[3px] text-[13px] font-bold text-slate">이벤트</span>
          {AUDIT_EVENT_TYPES.map((t) => {
            const on = events.includes(t)
            return (
              <div
                key={t}
                onClick={() => toggleEvent(t)}
                className="cursor-pointer rounded-full border px-[11px] py-[5px] font-mono text-[11.5px] font-extrabold"
                style={
                  on
                    ? { background: '#1E5FC2', color: '#FFFFFF', borderColor: '#1E5FC2' }
                    : { background: '#FFFFFF', color: '#475569', borderColor: '#CBD5E1' }
                }
              >
                {t}
              </div>
            )
          })}
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-bold text-slate">행위자</span>
            <div className="flex overflow-hidden rounded-lg border border-line-input bg-white">
              {['전체', 'SYSTEM', 'AGENT', 'HUMAN'].map((l) => (
                <div
                  key={l}
                  onClick={() => {
                    setActor(l)
                    resetPage()
                  }}
                  className="cursor-pointer px-[13px] py-[7px] text-[13px] font-bold"
                  style={
                    actor === l ? { background: '#1E5FC2', color: '#FFFFFF' } : { background: '#FFFFFF', color: '#475569' }
                  }
                >
                  {l}
                </div>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-bold text-slate">기간</span>
            <input
              type="date"
              value={from}
              onChange={(e) => {
                setFrom(e.target.value)
                resetPage()
              }}
              className="rounded-lg border border-line-input px-2.5 py-[7px] font-mono text-[13px] font-semibold text-navy"
            />
            <span className="font-bold text-[#94A3B8]">~</span>
            <input
              type="date"
              value={to}
              onChange={(e) => {
                setTo(e.target.value)
                resetPage()
              }}
              className="rounded-lg border border-line-input px-2.5 py-[7px] font-mono text-[13px] font-semibold text-navy"
            />
          </div>
          <input
            type="text"
            value={target}
            onChange={(e) => {
              setTarget(e.target.value)
              resetPage()
            }}
            placeholder="대상 ID 검색 (예: APR-0001)"
            className="w-[230px] rounded-lg border border-line-input px-3 py-2 font-mono text-[13px] font-semibold text-navy"
          />
        </div>
      </div>
      <div className="grid grid-cols-[7fr_3fr] items-start gap-4">
        <div className="overflow-hidden rounded-xl border border-line bg-white shadow-[0_1px_3px_rgba(15,42,92,.05)]">
          <div className="grid grid-cols-[150px_130px_210px_200px_1fr] gap-2 border-b border-line bg-page px-4 py-3 text-xs font-extrabold text-slate">
            <span>발생시각</span>
            <span>행위자</span>
            <span>이벤트</span>
            <span>대상</span>
            <span>요약</span>
          </div>
          {filtered.length === 0 && (
            <div className="p-12 text-center text-[15px] font-semibold text-slate">
              조건에 맞는 로그가 없습니다. 필터를 조정해 주세요.
            </div>
          )}
          {rows.map((e) => {
            const key = e.ev + e.entId + e.ts
            const ec = evColor(e.ev)
            const ac = acColor(e.ac)
            const isOpen = open === key
            return (
              <div key={key}>
                <div
                  onClick={() => setOpen(isOpen ? null : key)}
                  className="grid cursor-pointer grid-cols-[150px_130px_210px_200px_1fr] items-center gap-2 border-b border-line-soft px-4 py-2.5 transition-colors duration-[120ms] hover:bg-line-soft"
                  style={{ background: isOpen ? '#EDF2FA' : 'transparent' }}
                >
                  <span className="font-mono text-[12.5px] font-bold text-ink">{e.ts.slice(5)}</span>
                  <span className="flex items-center gap-1.5">
                    <span
                      className="flex h-5 w-5 flex-none items-center justify-center rounded-md text-[10.5px] font-extrabold"
                      style={{ background: ac[0], color: ac[1] }}
                    >
                      {e.ac[0]}
                    </span>
                    <span className="font-mono text-[12.5px] font-bold text-ink">{e.actor}</span>
                  </span>
                  <span>
                    <span
                      className="rounded-md px-2 py-[3px] font-mono text-[10.5px] font-extrabold"
                      style={{ background: ec[0], color: ec[1] }}
                    >
                      {e.ev}
                    </span>
                  </span>
                  <span className="font-mono text-[12.5px] font-semibold text-slate">
                    {e.entType} <span className="font-extrabold text-navy">{e.entId}</span>
                  </span>
                  <span className="text-[13px] font-semibold text-ink">{e.summary}</span>
                </div>
                {isOpen && (
                  <div className="animate-[om-fadein_.2s] border-b border-line bg-page px-[18px] py-3.5">
                    {e.diff ? (
                      <JsonDiff diff={e.diff} />
                    ) : (
                      <div className="text-[13px] font-semibold text-slate-light">
                        상태 변경 데이터 없음 (이벤트 기록 전용)
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
          <div className="flex items-center gap-1.5 px-4 py-3">
            <span className="mr-auto text-[12.5px] font-bold text-slate">총 {filtered.length}건</span>
            {Array.from({ length: pageCnt }, (_, i) => (
              <div
                key={i}
                onClick={() => {
                  setPage(i + 1)
                  setOpen(null)
                }}
                className="flex h-[30px] w-[30px] cursor-pointer items-center justify-center rounded-[7px] border font-mono text-[13px] font-extrabold"
                style={
                  curPage === i + 1
                    ? { background: '#1E5FC2', color: '#FFFFFF', borderColor: '#1E5FC2' }
                    : { background: '#FFFFFF', color: '#475569', borderColor: '#CBD5E1' }
                }
              >
                {i + 1}
              </div>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-3.5">
          <div className="grid grid-cols-2 gap-3.5">
            <div className="rounded-xl border border-line bg-white px-[17px] py-[15px] shadow-[0_1px_3px_rgba(15,42,92,.05)]">
              <div className="text-[12.5px] font-bold text-slate">오늘 이벤트</div>
              <div className="mt-1 font-mono text-3xl font-extrabold text-navy">{todayCnt}</div>
            </div>
            <div className="rounded-xl border border-line bg-white px-[17px] py-[15px] shadow-[0_1px_3px_rgba(15,42,92,.05)]">
              <div className="text-[12.5px] font-bold text-slate">실패 이벤트</div>
              <div className="mt-1 font-mono text-3xl font-extrabold text-oos">{failedCnt}</div>
            </div>
          </div>
          <div className="rounded-xl border border-line bg-white px-[18px] py-[17px] shadow-[0_1px_3px_rgba(15,42,92,.05)]">
            <div className="mb-[13px] text-sm font-extrabold text-navy">이벤트 유형별 건수</div>
            <div className="flex flex-col gap-[9px]">
              {typeCnts.map((b) => (
                <div key={b.name}>
                  <div className="flex items-center gap-1.5 font-mono text-[11px] font-extrabold text-slate">
                    <span>{b.name}</span>
                    <span className="ml-auto text-[12.5px] text-navy">{b.cnt}</span>
                  </div>
                  <div className="mt-[3px] h-2 overflow-hidden rounded bg-line-soft">
                    <div
                      className="h-full rounded"
                      style={{
                        width: `${(b.cnt / typeMax) * 100}%`,
                        background: b.name.includes('FAILED') ? '#DC2626' : '#1E5FC2',
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default AuditLogPage
