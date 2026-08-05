import { useEffect, useRef, useState } from 'react'
import { BarChart, Bar, XAxis, LabelList, ResponsiveContainer } from 'recharts'
import { postQuery, validateSql } from '../../../shared/api/analytics.js'
import { NL_CHIPS, NL_INITIAL_HISTORY } from '../mock/queries.js'

// dc.html SQL 하이라이팅 토크나이저
const KEYWORDS = ['SELECT', 'FROM', 'WHERE', 'GROUP', 'BY', 'ORDER', 'DESC', 'COUNT', 'AS', 'LIMIT', 'AND']
const highlightSql = (sql) =>
  sql.split('\n').map((line) =>
    line
      .split(/(\s+|,|\(|\)|\*|;|=)/)
      .filter((t) => t !== '')
      .map((t) => {
        if (KEYWORDS.includes(t.toUpperCase())) return { t, c: '#7DD8E5', w: 800 }
        if (/^'.*'$/.test(t)) return { t, c: '#FCD34D', w: 600 }
        if (/^\d+$/.test(t)) return { t, c: '#86EFAC', w: 700 }
        return { t, c: '#E2E8F0', w: 500 }
      }),
  )

function AnalyticsPage() {
  const [question, setQuestion] = useState('')
  const [activeQ, setActiveQ] = useState('')
  const [def, setDef] = useState(null)
  const [phase, setPhase] = useState(null) // gen | rejected | run | done
  const [tab, setTab] = useState('table')
  const [sortAsc, setSortAsc] = useState(false)
  const [history, setHistory] = useState(NL_INITIAL_HISTORY)
  // FR-D-09: 생성 SQL을 textarea로 수정 후 재검증 (시안에 없던 개선)
  const [sqlText, setSqlText] = useState('')
  const [editing, setEditing] = useState(false)
  const [reverified, setReverified] = useState(null)
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

  const pushHistory = (q, ok, rows, lat) =>
    setHistory((h) => (h.some((x) => x.q === q) ? h : [{ q, ok, rows, lat }, ...h]))

  const ask = (q) => {
    const query = (q ?? '').trim()
    if (!query) return
    clearTimers()
    setQuestion(query)
    setActiveQ(query)
    setPhase('gen')
    setTab('table')
    setSortAsc(false)
    setReverified(null)
    setEditing(false)
    postQuery(query).then((d) => {
      if (!d) {
        setPhase(null)
        return
      }
      after(400, () => {
        if (d.reject) {
          setPhase('rejected')
          pushHistory(query, false, 0, d.lat)
        } else {
          setDef(d)
          setSqlText(d.sql)
          setPhase('run')
          after(800, () => {
            setPhase('done')
            pushHistory(query, true, d.rows ? d.rows.length : 0, d.lat)
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
    validateSql(sqlText).then((res) => {
      setEditing(false)
      setReverified(res.message)
      after(2200, () => setReverified(null))
    })
  }

  const sorted =
    def && def.rows ? (def.noSort ? def.rows : [...def.rows].sort((a, b) => (sortAsc ? a[1] - b[1] : b[1] - a[1]))) : []
  const hasSql = (phase === 'run' || phase === 'done') && def && !def.reject
  const barData = sorted.map((r) => ({ label: String(r[0]), value: r[1] }))

  return (
    <div className="flex animate-[om-fadein_.3s_ease-out] flex-col gap-3.5">
      <div className="text-[21px] font-extrabold tracking-[-.3px] text-navy">
        자연어 분석{' '}
        <span className="ml-1.5 rounded-md border border-[#BFDBFE] bg-[#F0F6FF] px-2.5 py-[3px] align-middle font-mono text-[13px] font-extrabold text-brand">
          Text2SQL
        </span>
      </div>
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
          <span className="text-[13px] font-semibold text-slate-light">1/2 단계 · 스키마 매핑</span>
        </div>
      )}
      {phase === 'rejected' && (
        <div className="animate-[om-fadein_.25s] rounded-xl border-2 border-oos bg-white px-[22px] py-5 shadow-[0_4px_16px_rgba(220,38,38,.12)]">
          <div className="flex items-center gap-2.5">
            <span className="flex h-[34px] w-[34px] flex-none items-center justify-center rounded-full bg-oos-soft text-[19px] font-extrabold text-oos">
              !
            </span>
            <div className="text-[17px] font-extrabold text-oos">실행할 수 없는 요청입니다 — SELECT 외 구문은 거부됩니다</div>
            <span className="ml-auto rounded-md bg-oos-soft px-2.5 py-1 font-mono text-xs font-extrabold text-oos">
              REJECT_NON_SELECT
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
          <div className="overflow-hidden rounded-xl border border-line bg-white shadow-[0_1px_3px_rgba(15,42,92,.05)]">
            <div className="flex items-center gap-2.5 border-b border-line bg-page px-[18px] py-3">
              <span className="text-sm font-extrabold text-navy">생성 SQL</span>
              {reverified && (
                <span className="animate-[om-fadein_.2s] rounded-md bg-ok-soft px-2.5 py-[3px] text-xs font-extrabold text-ok">
                  {reverified}
                </span>
              )}
              <div className="ml-auto flex gap-2">
                {editing ? (
                  <>
                    <button
                      onClick={reverify}
                      className="cursor-pointer rounded-[7px] border-none bg-brand px-4 py-[7px] font-sans text-[13px] font-extrabold text-white hover:bg-brand-light"
                    >
                      재검증
                    </button>
                    <button
                      onClick={() => {
                        setEditing(false)
                        setSqlText(def.sql)
                      }}
                      className="cursor-pointer rounded-[7px] border border-line-input bg-white px-4 py-[7px] font-sans text-[13px] font-extrabold text-slate hover:bg-line-soft"
                    >
                      취소
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      onClick={run}
                      className="cursor-pointer rounded-[7px] border-none bg-brand px-4 py-[7px] font-sans text-[13px] font-extrabold text-white hover:bg-brand-light"
                    >
                      실행
                    </button>
                    <button
                      onClick={() => setEditing(true)}
                      className="cursor-pointer rounded-[7px] border border-[#BFDBFE] bg-white px-4 py-[7px] font-sans text-[13px] font-extrabold text-brand hover:bg-[#F0F6FF]"
                    >
                      SQL 수정 후 재검증
                    </button>
                  </>
                )}
              </div>
            </div>
            <div className="bg-navy px-5 py-4">
              {editing ? (
                <textarea
                  value={sqlText}
                  onChange={(e) => setSqlText(e.target.value)}
                  rows={Math.max(3, sqlText.split('\n').length)}
                  className="w-full resize-y rounded-md border border-[#2E4A7E] bg-transparent p-2 font-mono text-sm leading-[1.7] text-[#E2E8F0] focus:border-teal focus:outline-none"
                />
              ) : (
                highlightSql(sqlText).map((toks, i) => (
                  <div key={i} className="font-mono text-sm leading-[1.7]">
                    {toks.map((tk, j) => (
                      <span key={j} className="whitespace-pre" style={{ color: tk.c, fontWeight: tk.w }}>
                        {tk.t}
                      </span>
                    ))}
                  </div>
                ))
              )}
            </div>
            <div className="border-t border-line bg-page px-[18px] py-[9px] font-mono text-[12.5px] font-bold text-slate">
              읽기 전용 계정(kosa_readonly)으로 실행 · LIMIT 500 · timeout 5s
            </div>
          </div>
          {phase === 'run' && (
            <div className="flex items-center gap-3 rounded-xl border border-line bg-white p-[22px] shadow-[0_1px_3px_rgba(15,42,92,.05)]">
              <span className="h-[18px] w-[18px] animate-[om-spin_.8s_linear_infinite] rounded-full border-[3px] border-[#D5E2F5] border-t-ok" />
              <span className="text-[15px] font-bold text-navy">쿼리 실행 중...</span>
              <span className="text-[13px] font-semibold text-slate-light">2/2 단계 · kosa_readonly</span>
            </div>
          )}
          {phase === 'done' && (
            <div className="animate-[om-fadein_.25s] overflow-hidden rounded-xl border border-line bg-white shadow-[0_1px_3px_rgba(15,42,92,.05)]">
              <div className="flex items-center border-b border-line bg-page px-[18px]">
                <div
                  onClick={() => setTab('table')}
                  className="cursor-pointer border-b-[3px] px-[18px] py-3 text-sm font-extrabold"
                  style={{ color: tab === 'table' ? '#1E5FC2' : '#64748B', borderColor: tab === 'table' ? '#1E5FC2' : 'transparent' }}
                >
                  표
                </div>
                <div
                  onClick={() => setTab('chart')}
                  className="cursor-pointer border-b-[3px] px-[18px] py-3 text-sm font-extrabold"
                  style={{ color: tab === 'chart' ? '#1E5FC2' : '#64748B', borderColor: tab === 'chart' ? '#1E5FC2' : 'transparent' }}
                >
                  차트
                </div>
                <span className="ml-auto font-mono text-[12.5px] font-bold text-slate">
                  {def.rows ? `row_cnt ${def.rows.length} · ${def.lat}ms` : `row_cnt — · ${def.lat}ms`}
                </span>
              </div>
              {tab === 'table' && !def.rows && (
                <div className="p-8 text-center">
                  <div className="text-[15px] font-extrabold text-navy">계측 FAIL 6건 — 상세 목록 실측 미제공</div>
                  <div className="mt-1.5 text-[13.5px] font-semibold text-slate">
                    계측 결과 행 데이터를 주시면 표를 채웁니다 (PASS율 85.0% · 34/40 기준)
                  </div>
                </div>
              )}
              {tab === 'table' && def.rows && (
                <div className="flex flex-col gap-3.5 px-[18px] py-4">
                  <div className="min-w-[420px] self-start overflow-hidden rounded-[10px] border border-line">
                    <div className="flex border-b border-line bg-page">
                      {def.cols.map((c, i) => {
                        const sortable = !def.noSort && i === def.cols.length - 1
                        return (
                          <div
                            key={c}
                            onClick={sortable ? () => setSortAsc((s) => !s) : undefined}
                            className="flex flex-1 cursor-pointer items-center gap-1.5 px-4 py-2.5 font-mono text-[13px] font-extrabold text-navy hover:bg-line-soft"
                          >
                            {c}
                            {sortable && <span className="text-[10px] text-brand">{sortAsc ? '▲' : '▼'}</span>}
                          </div>
                        )
                      })}
                    </div>
                    {sorted.map((r, i) => (
                      <div key={i} className="flex border-b border-line-soft hover:bg-page">
                        {r.map((v, j) => (
                          <div
                            key={j}
                            className="flex-1 px-4 py-[9px] font-mono text-sm text-ink"
                            style={{ fontWeight: j === r.length - 1 ? 800 : 600 }}
                          >
                            {String(v)}
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                  {def.stats && (
                    <div className="flex gap-2.5">
                      {def.stats.map(([k, v]) => (
                        <div key={k} className="rounded-lg bg-page px-4 py-2 text-center">
                          <div className="font-mono text-[11px] font-bold text-slate-light">{k}</div>
                          <div className="mt-0.5 font-mono text-[17px] font-extrabold text-navy">{v}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {tab === 'chart' && (
                <div className="px-[22px] py-[18px]">
                  <div className="mb-3.5 flex items-center gap-2.5">
                    <span className="rounded-md border border-[#BFDBFE] bg-[#F0F6FF] px-2.5 py-[3px] font-mono text-xs font-extrabold text-brand">
                      {def.chart === 'bar' ? '자동 선택: bar' : '표로 강등됨'}
                    </span>
                    {(def.corrected || def.chart === 'demote') && (
                      <span className="rounded-md bg-ooc-soft px-2.5 py-[3px] text-[12.5px] font-bold text-ooc">
                        {def.corrected ? 'line → bar로 보정됨 (범주형 x축)' : '집계 형태가 차트와 호환되지 않아 표로 강등됨'}
                      </span>
                    )}
                  </div>
                  {def.chart === 'bar' && def.rows && (
                    <div className="h-[264px]">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={barData} margin={{ top: 24, right: 20, left: 20, bottom: 0 }} barCategoryGap="35%">
                          <XAxis
                            dataKey="label"
                            axisLine={{ stroke: '#CBD5E1', strokeWidth: 2 }}
                            tickLine={false}
                            tick={{ fill: '#475569', fontSize: 12.5, fontWeight: 700, fontFamily: 'ui-monospace, SF Mono, Menlo, monospace' }}
                          />
                          <Bar dataKey="value" fill="#1E5FC2" radius={[6, 6, 0, 0]} isAnimationActive maxBarSize={88}>
                            <LabelList
                              dataKey="value"
                              position="top"
                              style={{ fill: '#0F2A5C', fontSize: 15, fontWeight: 800, fontFamily: 'ui-monospace, SF Mono, Menlo, monospace' }}
                            />
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                  {def.chart === 'demote' && (
                    <div className="rounded-[10px] border border-dashed border-line-input bg-page p-7 text-center text-sm font-bold text-slate">
                      차트로 표현할 수 없는 결과 형태 — 표 탭을 이용하세요
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </>
      )}
      <div className="overflow-hidden rounded-xl border border-line bg-white shadow-[0_1px_3px_rgba(15,42,92,.05)]">
        <div className="border-b border-line bg-page px-[18px] py-3 text-sm font-extrabold text-navy">최근 질의 이력</div>
        <div className="grid grid-cols-[1fr_96px_84px_96px] gap-2 border-b border-line px-[18px] py-[9px] text-[11.5px] font-extrabold text-slate-light">
          <span>질문</span>
          <span>결과</span>
          <span className="text-right">row_cnt</span>
          <span className="text-right">latency_ms</span>
        </div>
        {history.map((h) => (
          <div key={h.q} className="grid grid-cols-[1fr_96px_84px_96px] items-center gap-2 border-b border-line-soft px-[18px] py-2.5">
            <span className="text-[13.5px] font-semibold text-ink">{h.q}</span>
            <span>
              <span
                className="rounded-md px-2 py-[3px] text-[11px] font-extrabold"
                style={h.ok ? { background: '#DCFCE7', color: '#16A34A' } : { background: '#FEE2E2', color: '#DC2626' }}
              >
                {h.ok ? '성공' : '거부'}
              </span>
            </span>
            <span className="text-right font-mono text-[13px] font-bold text-ink">{h.rows}</span>
            <span className="text-right font-mono text-[13px] font-bold text-slate">{h.lat.toLocaleString()}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default AnalyticsPage
