import { useEffect, useMemo, useRef, useState } from 'react'
import { postQuery, validateSql } from '../../../shared/api/analytics.js'
import { NL_CHIPS, NL_INITIAL_HISTORY, NL_REJECT_REASONS } from '../mock/queries.js'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import NlqSqlPanel from '../components/NlqSqlPanel.jsx'
import NlqResultTabs from '../components/NlqResultTabs.jsx'
import NlqHistoryPanel from '../components/NlqHistoryPanel.jsx'

// 0건 그룹 각주용 전체 챔버 목록 (설비 마스터 기준 4종)
const ALL_CHAMBERS = ['PHO-01-C1', 'PHO-01-C2', 'ETC-01-C1', 'ETC-01-C2']

const rejectReason = (code) => NL_REJECT_REASONS[code] ?? NL_REJECT_REASONS.REJECT_NON_SELECT

function AnalyticsPage() {
  const [question, setQuestion] = useState('')
  const [activeQ, setActiveQ] = useState('')
  const [def, setDef] = useState(null)
  const [rejectCode, setRejectCode] = useState('REJECT_NON_SELECT')
  const [phase, setPhase] = useState(null) // gen | unknown | rejected | run | done
  const [tab, setTab] = useState('table')
  const [sortAsc, setSortAsc] = useState(false)
  const [history, setHistory] = useState(NL_INITIAL_HISTORY)
  // 생성 SQL을 textarea로 수정 후 POST /analytics/validate 재호출
  const [sqlText, setSqlText] = useState('')
  const [editing, setEditing] = useState(false)
  const [checks, setChecks] = useState(null)
  const [validating, setValidating] = useState(false)
  const [verifyNotice, setVerifyNotice] = useState(null)
  const timers = useRef([])

  useEffect(() => {
    const t = timers.current
    return () => t.forEach(clearTimeout)
  }, [])
  const after = (ms, fn) => timers.current.push(setTimeout(fn, ms))
  const clearTimers = () => {
    timers.current.forEach(clearTimeout)
    timers.current = []
  }

  const pushHistory = (entry) =>
    setHistory((h) => (h.some((x) => x.q === entry.q) ? h : [entry, ...h]))

  // SQL 생성 직후 1회 자동 검증 (useEffect 아님 — 응답 콜백에서 수행)
  const verify = (sql, notice) => {
    setValidating(true)
    validateSql(sql).then((res) => {
      setValidating(false)
      setChecks(res.checks ?? [])
      if (notice) {
        const failed = (res.checks ?? []).filter((c) => !c.ok).length
        setVerifyNotice(res.valid ? { ok: true, text: '재검증 통과' } : { ok: false, text: `재검증 실패 ${failed}건` })
        after(2600, () => setVerifyNotice(null))
      }
    })
  }

  const ask = (q) => {
    const query = (q ?? '').trim()
    if (!query) return
    clearTimers()
    setQuestion(query)
    setActiveQ(query)
    setPhase('gen')
    setTab('table')
    setSortAsc(false)
    setEditing(false)
    setChecks(null)
    setVerifyNotice(null)
    postQuery(query).then((d) => {
      // mock에 없는 질문: 이력에 남기지 않고 안내 카드로 예시 칩 사용 유도
      if (!d) {
        setPhase('unknown')
        return
      }
      after(400, () => {
        if (d.reject) {
          const code = d.reject_code ?? 'REJECT_NON_SELECT'
          setRejectCode(code)
          setPhase('rejected')
          pushHistory({ q: query, ok: false, rows: 0, lat: d.lat, code, reason: rejectReason(code) })
        } else {
          setDef(d)
          setSqlText(d.sql)
          setPhase('run')
          verify(d.sql, false)
          after(800, () => {
            setPhase('done')
            pushHistory({ q: query, ok: true, rows: d.rows ? d.rows.length : 0, lat: d.lat })
          })
        }
      })
    })
  }

  const run = () => {
    if (!def) return
    clearTimers()
    setPhase('run')
    after(800, () => setPhase('done'))
  }

  const reverify = () => {
    setEditing(false)
    verify(sqlText, true)
  }

  const cancelEdit = () => {
    setEditing(false)
    if (def) setSqlText(def.sql)
  }

  const rows = useMemo(() => {
    if (!def || !def.rows) return []
    if (def.noSort) return def.rows
    return [...def.rows].sort((a, b) => (sortAsc ? a[1] - b[1] : b[1] - a[1]))
  }, [def, sortAsc])

  // 0건 그룹 각주 — 결과 첫 컬럼이 챔버 ID일 때만 계산
  const footnote = useMemo(() => {
    const list = def?.rows ?? []
    if (list.length === 0) return null
    if (!list.every((r) => ALL_CHAMBERS.includes(r[0]))) return null
    const missing = ALL_CHAMBERS.filter((c) => !list.some((r) => r[0] === c))
    if (missing.length === 0) return null
    return `${missing.join(', ')}는 기간 내 알람 0건이라 결과에 나오지 않는다`
  }, [def])

  const historyItems = useMemo(
    () => history.map((h) => (h.ok ? h : { ...h, reason: h.reason ?? rejectReason(h.code) })),
    [history],
  )

  const hasSql = (phase === 'run' || phase === 'done') && def && !def.reject

  return (
    <div className="flex animate-[om-fadein_.3s_ease-out] flex-col gap-3.5">
      <div className="flex flex-wrap items-center gap-2.5">
        <div className="text-[21px] font-extrabold tracking-[-.3px] text-navy">
          자연어 분석{' '}
          <span className="ml-1.5 rounded-md border border-[#BFDBFE] bg-[#F0F6FF] px-2.5 py-[3px] align-middle font-mono text-[13px] font-extrabold text-brand">
            Text2SQL
          </span>
        </div>
        <span className="ml-auto rounded-md border border-line bg-white px-3 py-[6px] font-mono text-[12.5px] font-extrabold text-slate">
          읽기 전용 · 허용 테이블 16종
        </span>
      </div>

      <div className="grid grid-cols-1 items-start gap-3.5 xl:grid-cols-[minmax(0,1fr)_312px]">
        <div className="flex min-w-0 flex-col gap-3.5">
          <div className="flex flex-col gap-3 rounded-xl border border-line bg-white px-5 py-[18px] shadow-[0_1px_3px_rgba(15,42,92,.05)]">
            <div className="flex gap-2.5">
              <input
                type="text"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') ask(e.target.value)
                }}
                placeholder="데이터에 대해 질문하세요 (예: 챔버별 알람 건수)"
                className="min-w-0 flex-1 rounded-[10px] border-[1.5px] border-line-input px-4 py-3 text-[15px] font-medium text-ink focus:border-brand focus:outline-none"
              />
              <button
                onClick={() => ask(question)}
                className="cursor-pointer rounded-[10px] border-none bg-brand px-[26px] py-3 font-sans text-[15px] font-extrabold text-white hover:bg-brand-light"
              >
                질문
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
              {NL_CHIPS.map((q) => {
                const danger = q.includes('지워줘')
                return (
                  <div
                    key={q}
                    onClick={() => ask(q)}
                    className="cursor-pointer rounded-full border px-3.5 py-1.5 text-[13px] font-bold hover:brightness-[.96]"
                    style={
                      danger
                        ? { background: '#FEF2F2', color: '#DC2626', borderColor: '#FECACA' }
                        : { background: '#F0F6FF', color: '#1E5FC2', borderColor: '#BFDBFE' }
                    }
                  >
                    {q}
                  </div>
                )
              })}
            </div>
          </div>

          {phase === 'gen' && (
            <div className="flex items-center gap-3 rounded-xl border border-line bg-white p-[22px] shadow-[0_1px_3px_rgba(15,42,92,.05)]">
              <span className="h-[18px] w-[18px] animate-[om-spin_.8s_linear_infinite] rounded-full border-[3px] border-[#D5E2F5] border-t-brand" />
              <span className="text-[15px] font-bold text-navy">SQL 생성 중...</span>
              <span className="text-[13px] font-semibold text-slate-light">1/2 단계 · LLM API 스키마 매핑</span>
            </div>
          )}

          {phase === 'unknown' && (
            <EmptyState
              title="이 질문은 데모 mock에 준비되지 않았습니다"
              description="위의 예시 질문을 사용해 주세요. (실제 API 연결 시에는 모든 자연어 질문이 처리됩니다)"
            />
          )}

          {phase === 'rejected' && (
            <div className="animate-[om-fadein_.25s] rounded-xl border-2 border-oos bg-white px-[22px] py-5 shadow-[0_4px_16px_rgba(220,38,38,.12)]">
              <div className="flex flex-wrap items-center gap-2.5">
                <span className="flex h-[34px] w-[34px] flex-none items-center justify-center rounded-full bg-oos-soft text-[19px] font-extrabold text-oos">
                  !
                </span>
                <div className="text-[17px] font-extrabold text-oos">
                  실행할 수 없는 요청입니다 — {rejectReason(rejectCode)}
                </div>
                <span className="ml-auto rounded-md bg-oos-soft px-2.5 py-1 font-mono text-xs font-extrabold text-oos">
                  {rejectCode}
                </span>
              </div>
              <div className="mt-3 rounded-lg bg-[#FEF2F2] px-3.5 py-[11px] text-sm font-semibold text-ink">
                요청 &quot;<span className="font-mono">{activeQ}</span>&quot; 은 데이터 변경(DELETE) 의도로 분류되어 SQL을
                생성하지 않았습니다.
              </div>
              <div className="mt-2.5 text-[13px] font-semibold text-slate">
                ℹ 구문 오류는 1회 자동 교정을 시도하지만, 정책 위반은 즉시 거부됩니다
              </div>
            </div>
          )}

          {hasSql && (
            <>
              <NlqSqlPanel
                sqlText={sqlText}
                onSqlChange={setSqlText}
                editing={editing}
                onStartEdit={() => setEditing(true)}
                onCancelEdit={cancelEdit}
                onRun={run}
                onReverify={reverify}
                checks={checks}
                validating={validating}
                verifyNotice={verifyNotice}
              />
              {phase === 'run' && (
                <div className="flex items-center gap-3 rounded-xl border border-line bg-white p-[22px] shadow-[0_1px_3px_rgba(15,42,92,.05)]">
                  <span className="h-[18px] w-[18px] animate-[om-spin_.8s_linear_infinite] rounded-full border-[3px] border-[#D5E2F5] border-t-ok" />
                  <span className="text-[15px] font-bold text-navy">쿼리 실행 중...</span>
                  <span className="text-[13px] font-semibold text-slate-light">2/2 단계 · kosa_readonly</span>
                </div>
              )}
              {phase === 'done' && (
                <NlqResultTabs
                  def={def}
                  tab={tab}
                  onTab={setTab}
                  sortAsc={sortAsc}
                  onToggleSort={() => setSortAsc((s) => !s)}
                  rows={rows}
                  footnote={footnote}
                />
              )}
            </>
          )}
        </div>

        <NlqHistoryPanel items={historyItems} activeQ={activeQ} onRerun={ask} />
      </div>
    </div>
  )
}

export default AnalyticsPage
