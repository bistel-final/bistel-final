import { useCallback, useEffect, useRef, useState } from 'react'
import { getRuns, getApprovals, decideApproval } from '../../../shared/api/agent.js'
import { ACTION_DISPLAY, FAULT_BY_SENSOR } from '../mock/actions.js'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'

const runStColor = (s) =>
  s === 'COMPLETED' ? ['#DCFCE7', '#16A34A'] : s === 'WAITING_APPROVAL' ? ['#FEF3C7', '#D97706'] : ['#FEE2E2', '#DC2626']
const aprStColor = (s) =>
  s === 'PENDING' ? ['#FEF3C7', '#D97706'] : s === 'APPROVED' ? ['#DCFCE7', '#16A34A'] : ['#F1F5F9', '#64748B']
const histColor = (k) => (k === 'SENT' ? ['#DCFCE7', '#16A34A'] : k === 'FAILED' ? ['#FEE2E2', '#DC2626'] : ['#F1F5F9', '#64748B'])
const toolStColor = (s) =>
  s === 'OK' ? ['#DCFCE7', '#16A34A'] : s === 'FAILED' ? ['#FEE2E2', '#DC2626'] : ['#F1F5F9', '#64748B']

// dc.html Tool 호출 타임라인 (FAILED 런은 search_documents TIMEOUT → decide_action SKIPPED)
const toolsOf = (failed) => [
  { n: '1', name: 'get_fdc_summary', st: 'OK', ms: '412ms' },
  { n: '2', name: 'get_equipment_context', st: 'OK', ms: '388ms' },
  failed
    ? { n: '3', name: 'search_documents', st: 'FAILED', ms: '30,012ms' }
    : { n: '3', name: 'search_documents', st: 'OK', ms: '1,240ms' },
  failed
    ? { n: '4', name: 'decide_action', st: 'SKIPPED', ms: '—', tag: true }
    : { n: '4', name: 'decide_action', st: 'OK', ms: '6ms', tag: true },
]

function AgentPage() {
  const [runs, setRuns] = useState(null)
  const [approvals, setApprovals] = useState(null)
  const [history, setHistory] = useState([])
  const [error, setError] = useState(null)
  const [selRun, setSelRun] = useState('RUN-0006')
  const [aprState, setAprState] = useState({}) // { id: { st, send } }
  const [comments, setComments] = useState({})
  const [deciders, setDeciders] = useState({})
  const [toast, setToast] = useState(false)
  const timers = useRef([])

  const load = useCallback(() => {
    Promise.all([getRuns(), getApprovals()])
      .then(([r, ap]) => {
        setRuns(r)
        setApprovals(ap.approvals)
        setHistory(ap.history)
        setAprState(Object.fromEntries(ap.approvals.map((a) => [a.id, { st: a.status, send: null }])))
      })
      .catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    load()
  }, [load])
  useEffect(() => {
    const t = timers.current
    return () => t.forEach(clearTimeout)
  }, [])
  const after = (ms, fn) => timers.current.push(setTimeout(fn, ms))

  if (error) return <ErrorState detail={error} onRetry={() => { setError(null); load() }} />
  if (!runs || !approvals) return <LoadingState message="Agent 실행 데이터를 불러오는 중…" />

  const run = runs.find((r) => r.run_id === selRun) ?? runs[5]
  const failed = run.status === 'FAILED'
  const chain = run.run_id === 'RUN-0008'
  const fault = FAULT_BY_SENSOR[run.sensor_id]
  const act = ACTION_DISPLAY[run.action_id]
  const actWhy = act ? (act.why ?? fault.why) : fault.why
  const dc = runStColor(run.status)
  const cause = failed
    ? 'search_documents Tool 장애(TIMEOUT)로 실행 실패. 자동 재처리는 금지 정책이며, 수동 재실행(RUN-0003)으로 완료 처리되었습니다.'
    : chain
      ? 'LOT-260008 ETCH(ETC-01-C1) ET_CF4 R02_OOC 감지. get_equipment_context의 UPSTREAM_OF 조회 결과, 같은 LOT의 상류 PHOTO 공정 이상이 주 기여 원인으로 판단된 연쇄 이상 케이스.'
      : `${run.rerun ? 'FAILED된 RUN-0002의 수동 재실행 건. ' : ''}${fault.basis}. 발생 챔버 ${run.chamber_id}, ${run.recipe_step_name} 스텝에서 감지.`

  const showToast = () => {
    setToast(true)
    after(2500, () => setToast(false))
  }

  const approve = (a) => {
    if (aprState[a.id].st !== 'PENDING') {
      showToast()
      return
    }
    decideApproval(a.id, { decision: 'APPROVED', decided_by: deciders[a.id] || '', comment: comments[a.id] || '' })
    const set = (send) => setAprState((s) => ({ ...s, [a.id]: { st: 'APPROVED', send } }))
    set('WAITING')
    after(1000, () => set('SENDING'))
    after(2000, () => {
      set('SENT')
      setHistory((h) => [{ k: 'SENT', label: `${a.action_id} · ${a.alarm_id} · EQP_HOLD` }, ...h])
    })
  }

  const reject = (a) => {
    if (aprState[a.id].st !== 'PENDING') {
      showToast()
      return
    }
    decideApproval(a.id, { decision: 'REJECTED', decided_by: deciders[a.id] || '', comment: comments[a.id] || '' })
    setAprState((s) => ({ ...s, [a.id]: { st: 'REJECTED', send: 'CANCELED' } }))
    setHistory((h) => [{ k: 'CANCELED', label: `${a.action_id} · ${a.alarm_id} · EQP_HOLD` }, ...h])
  }

  const pendCnt = approvals.filter((a) => aprState[a.id]?.st === 'PENDING').length

  return (
    <div className="flex animate-[om-fadein_.3s_ease-out] flex-col gap-3.5">
      <div className="text-[21px] font-extrabold tracking-[-.3px] text-navy">Agent 분석·승인</div>
      <div className="grid grid-cols-[290px_1fr_360px] items-start gap-4">
        <div className="overflow-hidden rounded-xl border border-line bg-white shadow-[0_1px_3px_rgba(15,42,92,.05)]">
          <div className="flex items-center gap-2 border-b border-line bg-page px-4 py-[13px]">
            <span className="text-sm font-extrabold text-navy">Agent 실행</span>
            <span className="ml-auto font-mono text-xs font-bold text-slate">{runs.length}건</span>
          </div>
          <div className="max-h-[640px] overflow-auto">
            {runs.map((r) => {
              const c = runStColor(r.status)
              const on = selRun === r.run_id
              return (
                <div
                  key={r.run_id}
                  onClick={() => setSelRun(r.run_id)}
                  className="cursor-pointer border-b border-line-soft border-l-[3px] px-3.5 py-[11px] transition-colors duration-[120ms] hover:bg-line-soft"
                  style={{ background: on ? '#DBEAFE' : 'transparent', borderLeftColor: on ? '#1E5FC2' : 'transparent' }}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[13.5px] font-extrabold text-navy">{r.run_id}</span>
                    <span
                      className="ml-auto rounded-[5px] px-[7px] py-0.5 text-[10.5px] font-extrabold"
                      style={{ background: c[0], color: c[1] }}
                    >
                      {r.status}
                    </span>
                  </div>
                  <div className="mt-1 font-mono text-[12.5px] font-semibold text-slate">
                    {r.alarm_id} · {r.lot_id}
                  </div>
                  <div className="mt-0.5 flex gap-2 font-mono text-xs font-semibold text-slate-light">
                    <span>{r.chamber_id}</span>
                    <span className="ml-auto">
                      {r.model} · {(r.latency_ms / 1000).toFixed(1)}s
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
        <div className="flex flex-col gap-4 rounded-xl border border-line bg-white px-[22px] py-5 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
          <div className="flex items-center gap-3">
            <div className="font-mono text-base font-extrabold text-navy">{run.run_id}</div>
            <span
              className="rounded-md px-[9px] py-[3px] font-mono text-xs font-extrabold"
              style={{ background: dc[0], color: dc[1] }}
            >
              {run.status}
            </span>
            {failed && (
              <button
                onClick={() => setSelRun('RUN-0003')}
                className="cursor-pointer rounded-md border border-[#BFDBFE] bg-white px-3 py-1 font-sans text-xs font-extrabold text-brand hover:bg-[#F0F6FF]"
              >
                수동 재실행 결과 보기 (RUN-0003)
              </button>
            )}
            <span className="ml-auto font-mono text-[12.5px] font-bold text-slate">
              {run.alarm_id} · {run.lot_id} · {run.chamber_id}
            </span>
          </div>
          <div className="flex items-center gap-3.5 rounded-[10px] border border-[#BFDBFE] bg-[#F0F6FF] p-4">
            <span className="rounded-[10px] bg-brand px-[18px] py-2.5 font-mono text-2xl font-extrabold text-white">
              {failed ? '—' : fault.code}
            </span>
            <div>
              <div className="text-base font-extrabold text-navy">{failed ? '분류 미완료' : fault.name}</div>
              <div className="mt-[3px] text-[13.5px] font-semibold text-slate">
                {failed ? 'search_documents Tool 장애로 분류 전 중단' : fault.basis}
              </div>
            </div>
          </div>
          <div>
            <div className="mb-1.5 text-[13.5px] font-extrabold text-navy">원인 분석</div>
            <div className="text-[14.5px] font-medium leading-[1.6] text-ink">{cause}</div>
          </div>
          {chain && (
            <div className="overflow-hidden rounded-[10px] border border-[#BFDBFE]">
              <div className="bg-[#F0F6FF] px-3.5 py-2.5 text-[13px] font-extrabold text-brand">
                상류 기여 확인 (LOT-260008)
              </div>
              <div className="flex flex-col gap-2 px-3.5 py-3">
                <div className="flex items-center gap-2 font-mono text-[13px] font-semibold text-ink">
                  <span className="h-1.5 w-1.5 flex-none rounded-full bg-brand" />
                  PHO-01-C1 FOC 이력: ALM-0025 (PH_FOCUS R01_OOS) → LOT_HOLD 조치 ACT-0007
                </div>
                <div className="flex items-center gap-2 font-mono text-[13px] font-semibold text-ink">
                  <span className="h-1.5 w-1.5 flex-none rounded-full bg-oos" />
                  계측 FAIL: MET-0029 (CD_ADI 41.59) · MET-0031 (CD_AEI 41.20) — WAFER 1
                </div>
              </div>
              <div className="mx-3.5 mb-3.5 rounded-r-lg border-l-[3px] border-navy bg-page px-3.5 py-[11px] text-[13.5px] font-semibold leading-[1.55] text-navy">
                판단 결론: 하류 ETCH 자체 OOC는 존재하므로 자체 조치 MONITOR 유지, 상류 원인이 주 기여 — EQP_HOLD
                미적용, PHOTO LOT_HOLD 중복 미생성
              </div>
            </div>
          )}
          <div className="flex items-center gap-2.5 rounded-[10px] bg-page px-3.5 py-3">
            <span className="text-[13px] font-bold text-slate">권고 조치</span>
            {failed || !act ? (
              <span className="text-[13px] font-semibold text-slate-light">실측 미제공</span>
            ) : (
              <>
                <span className="rounded-md bg-navy px-3 py-1 font-mono text-[13px] font-extrabold text-white">
                  {act.code}
                </span>
                <span className="text-[13px] font-semibold text-slate">{actWhy}</span>
              </>
            )}
          </div>
          <div>
            <div className="mb-2 text-[13.5px] font-extrabold text-navy">사용 근거</div>
            <div className="flex flex-col gap-2">
              <div className="rounded-lg border border-line px-3.5 py-[11px]">
                <div className="mb-1 text-xs font-extrabold text-brand">센서 요약</div>
                <div className="font-mono text-[13px] font-semibold text-ink">
                  {run.detail} · hit_cnt {run.hit_cnt}
                </div>
              </div>
              <div className="rounded-lg border border-line px-3.5 py-[11px]">
                <div className="mb-1 text-xs font-extrabold text-brand">장비 관계</div>
                <div className="font-mono text-[13px] font-semibold text-ink">
                  PHO-01 (PH-9000) → UPSTREAM_OF → ETC-01 (ET-7500) · 발생 챔버 {run.chamber_id}
                </div>
              </div>
              <div className="rounded-lg border border-dashed border-line-input bg-page px-3.5 py-[11px]">
                <div className="mb-1 text-xs font-extrabold text-slate-light">문서 인용</div>
                <div className="text-[13px] font-semibold text-slate">실측 문서(문서명·절·발췌) 대기 중 — 제공 시 채웁니다</div>
              </div>
            </div>
          </div>
          <div>
            <div className="mb-2 text-[13.5px] font-extrabold text-navy">Tool 호출 타임라인</div>
            <div className="flex flex-col">
              {toolsOf(failed).map((t) => {
                const tc = toolStColor(t.st)
                const hl = chain && t.name === 'get_equipment_context'
                return (
                  <div
                    key={t.n}
                    className="flex items-center gap-2.5 rounded-md border-b border-line-soft p-2"
                    style={{ background: hl ? '#F0F6FF' : 'transparent' }}
                  >
                    <span
                      className="flex h-[22px] w-[22px] flex-none items-center justify-center rounded-full text-[11px] font-extrabold"
                      style={{ background: tc[0], color: tc[1] }}
                    >
                      {t.n}
                    </span>
                    <span className="font-mono text-[13.5px] font-bold text-navy">{t.name}</span>
                    {t.tag && (
                      <span className="rounded-[5px] bg-line-soft px-[7px] py-0.5 text-[10.5px] font-extrabold text-slate">
                        규칙 코드
                      </span>
                    )}
                    {hl && (
                      <span className="rounded-[5px] bg-[#DBEAFE] px-[7px] py-0.5 text-[10.5px] font-extrabold text-brand">
                        UPSTREAM_OF 조회
                      </span>
                    )}
                    <span
                      className="ml-auto rounded-[5px] px-2 py-0.5 text-[11px] font-extrabold"
                      style={{ background: tc[0], color: tc[1] }}
                    >
                      {t.st}
                    </span>
                    <span className="w-16 text-right font-mono text-[12.5px] font-bold text-slate">{t.ms}</span>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
        <div className="flex flex-col gap-3.5">
          <div className="overflow-hidden rounded-xl border border-line bg-white shadow-[0_1px_3px_rgba(15,42,92,.05)]">
            <div className="flex items-center gap-2 border-b border-line bg-page px-4 py-[13px]">
              <span className="text-sm font-extrabold text-navy">승인 큐</span>
              <span className="ml-auto flex items-center gap-1.5 text-xs font-extrabold text-ooc">
                <span className="h-[7px] w-[7px] animate-[om-pulse_1.6s_infinite] rounded-full bg-ooc" />
                PENDING {pendCnt}
              </span>
            </div>
            {approvals.map((a) => {
              const st = aprState[a.id]?.st ?? 'PENDING'
              const send = aprState[a.id]?.send
              const sc = aprStColor(st)
              const stageIdx = ['WAITING', 'SENDING', 'SENT'].indexOf(send)
              return (
                <div
                  key={a.id}
                  className="border-b border-line-soft px-4 py-3.5 transition-opacity duration-300"
                  style={{ opacity: st === 'REJECTED' ? 0.55 : 1 }}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm font-extrabold text-navy">{a.id}</span>
                    <span className="font-mono text-[12.5px] font-bold text-slate">→ {a.action_id}</span>
                    <span
                      className="ml-auto rounded-md px-[9px] py-[3px] text-[11px] font-extrabold transition-colors duration-300"
                      style={{ background: sc[0], color: sc[1] }}
                    >
                      {st}
                    </span>
                  </div>
                  <div className="mt-[5px] font-mono text-[12.5px] font-semibold text-slate">{a.meta}</div>
                  {st === 'APPROVED' && (
                    <div className="mt-2.5 flex items-center gap-1.5">
                      {['WAITING', 'SENDING', 'SENT'].map((l, i) => (
                        <span key={l} className="contents">
                          <span
                            className="rounded-full px-2.5 py-1 text-[11px] font-extrabold transition-colors duration-[400ms]"
                            style={{
                              background: i < stageIdx ? '#DCFCE7' : i === stageIdx ? (l === 'SENT' ? '#16A34A' : '#1E5FC2') : '#F1F5F9',
                              color: i < stageIdx ? '#16A34A' : i === stageIdx ? '#FFFFFF' : '#94A3B8',
                            }}
                          >
                            {l}
                          </span>
                          {i < 2 && <span className="text-[11px] font-extrabold text-[#94A3B8]">→</span>}
                        </span>
                      ))}
                    </div>
                  )}
                  {st === 'REJECTED' && (
                    <div className="mt-2.5 inline-block rounded-md bg-[#F1F5F9] px-2.5 py-[5px] text-[11.5px] font-extrabold text-slate-light">
                      전송상태 CANCELED
                    </div>
                  )}
                  <div className="mt-3 flex flex-col gap-2">
                    <input
                      type="text"
                      value={comments[a.id] || ''}
                      onChange={(e) => setComments((s) => ({ ...s, [a.id]: e.target.value }))}
                      placeholder="코멘트"
                      className="rounded-[7px] border border-line-input px-[11px] py-2 text-[13px] font-medium text-ink"
                    />
                    <div className="flex gap-2">
                      <input
                        type="text"
                        value={deciders[a.id] || ''}
                        onChange={(e) => setDeciders((s) => ({ ...s, [a.id]: e.target.value }))}
                        placeholder="승인자 이름"
                        className="min-w-0 flex-1 rounded-[7px] border border-line-input px-[11px] py-2 text-[13px] font-medium text-ink"
                      />
                      <button
                        onClick={() => approve(a)}
                        className="cursor-pointer rounded-[7px] border-none bg-brand px-4 py-2 font-sans text-[13px] font-extrabold text-white hover:bg-brand-light"
                      >
                        승인
                      </button>
                      <button
                        onClick={() => reject(a)}
                        className="cursor-pointer rounded-[7px] border border-[#FECACA] bg-white px-4 py-2 font-sans text-[13px] font-extrabold text-oos hover:bg-[#FEF2F2]"
                      >
                        반려
                      </button>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
          <div className="overflow-hidden rounded-xl border border-line bg-white shadow-[0_1px_3px_rgba(15,42,92,.05)]">
            <div className="border-b border-line bg-page px-4 py-[13px] text-sm font-extrabold text-navy">전송 결과 이력</div>
            {history.map((h, i) => {
              const hc = histColor(h.k)
              return (
                <div key={`${h.k}-${h.label}-${i}`} className="flex items-center gap-2.5 border-b border-line-soft px-4 py-2.5">
                  <span
                    className="rounded-md px-[9px] py-[3px] text-[11px] font-extrabold"
                    style={{ background: hc[0], color: hc[1] }}
                  >
                    {h.k}
                  </span>
                  <span className="font-mono text-[12.5px] font-bold text-ink">{h.label}</span>
                  {h.k === 'FAILED' && (
                    <button className="ml-auto cursor-pointer rounded-md border border-[#BFDBFE] bg-white px-3 py-1 font-sans text-xs font-extrabold text-brand hover:bg-[#F0F6FF]">
                      재시도
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </div>
      {toast && (
        <div className="fixed bottom-7 right-7 z-40 animate-[om-fadein_.2s] rounded-[10px] border-l-4 border-oos bg-navy px-5 py-3.5 text-sm font-bold text-white shadow-[0_10px_30px_rgba(15,42,92,.35)]">
          409: 이미 처리된 승인 건입니다
        </div>
      )}
    </div>
  )
}

export default AgentPage
