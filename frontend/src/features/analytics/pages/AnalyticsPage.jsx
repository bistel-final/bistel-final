// 자연어 분석 — 디자인 v2 06 (읽기 전용 · 허용 테이블 16종)
// 응답 스키마는 명세 AnalysisQueryResponse:
//   {question, sql, columns, rows(dict[]), row_count, metric, metric_result, group_by, visualization, latency_ms}
// 거부 응답은 {question, rejected, reason, latency_ms} 뿐이다 (sql·rows 없음).
import { useEffect, useMemo, useRef, useState } from 'react'
import { getEvaluations, getQueryHistory, postQuery, validateSql } from '../../../shared/api/analytics.js'
import { NL_CHIPS } from '../mock/queries.js'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import Badge from '../../../shared/components/ui/Badge.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card } from '../../../shared/components/ui/Card.jsx'
import NlqSqlPanel from '../components/NlqSqlPanel.jsx'
import { formatSql } from '../components/sqlFormat.js'
import NlqResultTabs from '../components/NlqResultTabs.jsx'
import NlqHistoryPanel from '../components/NlqHistoryPanel.jsx'
import NlqEvaluationPanel from '../components/NlqEvaluationPanel.jsx'
import NlqPipeline from '../components/NlqPipeline.jsx'
import { GRAPH_CHAMBERS } from '../../../shared/graph/ontology-chambers.js'

// 0건 그룹 각주용 전체 챔버 목록은 온톨로지 정본과 공유한다.
const ALL_CHAMBERS = GRAPH_CHAMBERS

// reason 은 "POLICY_REJECTED: 사유" 형태다 — 접두어는 배지로, 뒤 문장은 사유로 나눠 쓴다
const reasonCode = (reason) => String(reason ?? '').split(':')[0].trim()
const reasonText = (reason) => String(reason ?? '').split(':').slice(1).join(':').trim() || String(reason ?? '')

// 정렬·차트 기준 컬럼 — visualization.y 우선, 없으면 마지막 컬럼
const yColumnOf = (d) => {
  const columns = d?.columns ?? []
  const y = d?.visualization?.y
  return columns.includes(y) ? y : columns[columns.length - 1]
}

// 단계 진행 표시 카드 (SQL 생성 / 쿼리 실행)
function PhaseCard({ label, note }) {
  return (
    <Card className="flex items-center gap-3 p-[22px]">
      <span className="h-[18px] w-[18px] animate-[om-spin_.8s_linear_infinite] rounded-full border-[3px] border-tint-blue border-t-blue" />
      <span className="text-sm font-bold text-navy">{label}</span>
      <span className="text-xs text-g1">{note}</span>
    </Card>
  )
}

function AnalyticsPage() {
  const [question, setQuestion] = useState('')
  const [activeQ, setActiveQ] = useState('')
  const [def, setDef] = useState(null)
  const [rejected, setRejected] = useState(null) // 거부 응답 원본 {question, rejected, reason, latency_ms}
  const [phase, setPhase] = useState(null) // gen | unknown | rejected | run | done
  const [tab, setTab] = useState('table')
  // 표 정렬 — null 이면 서버가 준 순서(SQL ORDER BY) 그대로. 정렬은 답의 일부라 임의로 바꾸지 않고,
  // 사용자가 헤더를 클릭했을 때만 desc → asc → 원래 순서로 순환한다.
  const [sortDir, setSortDir] = useState(null)
  // 이력은 GET /analytics/history 실응답으로 hydrate 한다 (V5-D-2.6, Mock 0).
  // 세션 내 신규 질의는 pushHistory 로 즉시 반영 — 서버 기록과 question 기준 dedupe.
  const [history, setHistory] = useState([])
  const [historyState, setHistoryState] = useState('loading') // loading | error | ready
  // 이력 — 서버 로그는 같은 질문이 수십 번 반복되므로 한 번에 넘치게 받아 질문 기준으로 접고(최신 1건만),
  // 고유 질문 20개를 4개씩 5페이지로 보인다. 페이지는 클라이언트에서 자른다.
  const [histPage, setHistPage] = useState(1)
  const HIST_PAGE = 4
  const HIST_MAX = 20
  const HIST_FETCH = 100 // 서버 size 상한
  const [sideTab, setSideTab] = useState('history') // history | evaluation — 보조 탭(8번째 메뉴 아님)
  // 생성 SQL을 textarea로 수정 후 POST /analytics/validate 재호출
  const [sqlText, setSqlText] = useState('')
  const [editing, setEditing] = useState(false)
  const [validation, setValidation] = useState(null) // {valid, reason, checks}
  const [validating, setValidating] = useState(false)
  const [verifyNotice, setVerifyNotice] = useState(null)
  const timers = useRef([])

  useEffect(() => {
    const t = timers.current
    return () => t.forEach(clearTimeout)
  }, [])

  // 이력 hydrate — 응답 계약(NlQueryLogItem)을 패널 항목 {question, ok, reason} 으로 접고 질문 기준 dedupe.
  useEffect(() => {
    let cancelled = false
    getQueryHistory({ page: 1, size: HIST_FETCH })
      .then((res) => {
        if (cancelled) return
        const seen = new Set()
        const items = []
        for (const it of res?.items ?? []) {
          if (seen.has(it.question)) continue
          seen.add(it.question)
          items.push({
            question: it.question,
            ok: !it.is_rejected && !it.error_msg,
            reason: it.reject_reason ?? it.error_msg ?? null,
            logged: true,
          })
        }
        setHistory((h) => {
          // 서버가 모르는 세션 항목(기록 실패 등)을 위에 유지하고 dedupe
          const session = h.filter((x) => x.logged === false)
          const own = new Set(session.map((x) => x.question))
          return [...session, ...items.filter((it) => !own.has(it.question))].slice(0, HIST_MAX)
        })
        setHistoryState('ready')
      })
      .catch(() => {
        if (!cancelled) setHistoryState('error')
      })
    return () => {
      cancelled = true
    }
  }, [])
  const after = (ms, fn) => timers.current.push(setTimeout(fn, ms))
  const clearTimers = () => {
    timers.current.forEach(clearTimeout)
    timers.current = []
  }

  // 새 질의는 목록 맨 위로. 같은 질문이 이미 있으면 그것을 위로 올린다 (중복 항목 생성 없음).
  const pushHistory = (entry) => {
    setHistory((h) => [entry, ...h.filter((x) => x.question !== entry.question)].slice(0, HIST_MAX))
    setHistPage(1)
  }

  // SQL 생성 직후 1회 자동 검증 (useEffect 아님 — 응답 콜백에서 수행)
  const verify = (sql, notice) => {
    setValidating(true)
    validateSql(sql).then((res) => {
      setValidating(false)
      setValidation({ valid: res.valid, reason: res.reason ?? '', checks: res.checks ?? [] })
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
    setSortDir(null)
    setEditing(false)
    setValidation(null)
    setVerifyNotice(null)
    postQuery(query)
      .then((d) => {
        // mock 모드에서 준비되지 않은 질문: 안내 카드로 예시 칩 사용 유도
        if (!d) {
          setPhase('unknown')
          return
        }
        after(400, () => {
          if (d.is_rejected) {
            setRejected(d)
            setPhase('rejected')
            pushHistory({
              question: query,
              ok: false,
              row_count: 0,
              latency_ms: d.latency_ms,
              reason: d.reject_reason,
              logged: d.nl_query_log_id != null,
            })
          } else if (d.error_msg) {
            // 검증은 통과했으나 DB 실행에서 실패 — 거부와 구분되는 상태
            setDef(d)
            setSqlText(formatSql(d.generated_sql ?? ''))
            setPhase('exec_error')
            pushHistory({
              question: query,
              ok: false,
              row_count: 0,
              latency_ms: d.latency_ms,
              reason: d.error_msg,
              logged: d.nl_query_log_id != null,
            })
          } else {
            setDef(d)
            setSqlText(formatSql(d.generated_sql ?? ''))
            // 기본 탭은 서버가 정한 차트 유형을 따른다 — 그릴 수 있는 유형(line/bar/histogram)이면 차트 부터
            setTab(['line', 'bar', 'histogram'].includes(d.visualization?.chart_type) ? 'chart' : 'table')
            setPhase('run')
            verify(d.generated_sql, false)
            after(800, () => {
              setPhase('done')
              pushHistory({
                question: query,
                ok: true,
                row_count: d.row_count ?? (d.rows ?? []).length,
                latency_ms: d.latency_ms,
                reason: null,
                logged: d.nl_query_log_id != null,
              })
            })
          }
        })
      })
      .catch(() => {
        // 네트워크·422 등 HTTP 수준 실패 — 로딩에 갇히지 않게 명시 상태로
        setPhase('failed')
      })
  }

  const reverify = () => {
    setEditing(false)
    verify(sqlText, true)
  }

  // 수정 SQL 을 passthrough 경로로 실행 — 검증(6종)을 포함해 결과·이력까지 갱신된다.
  // 검증 실패 SQL 은 서버가 POLICY_REJECTED 로 거부하므로 안전 경로는 동일하다.
  const runEdited = () => {
    setEditing(false)
    ask(sqlText)
  }

  const cancelEdit = () => {
    setEditing(false)
    if (def) setSqlText(formatSql(def.generated_sql ?? ''))
  }

  // rows 는 객체 배열이다 — 정렬 키는 컬럼명으로 접근한다
  const sortKey = yColumnOf(def)
  const rows = useMemo(() => {
    const list = def?.rows ?? []
    if (!sortDir || !sortKey || list.length < 2) return list
    if (!list.every((r) => typeof r[sortKey] === 'number')) return list
    return [...list].sort((a, b) => (sortDir === 'asc' ? a[sortKey] - b[sortKey] : b[sortKey] - a[sortKey]))
  }, [def, sortDir, sortKey])

  // 0건 그룹 각주 — 그룹 컬럼 값이 전부 챔버 ID일 때만 계산
  const footnote = useMemo(() => {
    const list = def?.rows ?? []
    const key = def?.visualization?.x ?? def?.group_by?.[0] ?? def?.columns?.[0]
    if (!key || list.length === 0) return null
    if (!list.every((r) => ALL_CHAMBERS.includes(r[key]))) return null
    const missing = ALL_CHAMBERS.filter((c) => !list.some((r) => r[key] === c))
    if (missing.length === 0) return null
    return `${missing.join(', ')}는 기간 내 알람 0건이라 결과에 나오지 않는다`
  }, [def])

  const hasSql = (phase === 'run' || phase === 'done' || phase === 'exec_error') && def && !def.is_rejected

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center pb-1.5 pt-3.5">
        <div className="text-[22px] font-extrabold tracking-[-.01em] text-ink">자연어 분석</div>
      </div>

      <Card className="px-6 py-5">
        <div className="flex gap-3">
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') ask(e.target.value)
            }}
            placeholder="데이터에 대해 질문하세요"
            className="h-[52px] min-w-0 flex-1 rounded-lg border border-line bg-white px-4 text-[15px] text-ink placeholder:text-faint focus:border-blue focus:outline-none focus:ring-2 focus:ring-tint-blue"
          />
          <Button onClick={() => ask(question)} className="flex-none text-[14px]" style={{ height: 52, padding: '0 36px' }}>
            질문
          </Button>
        </div>
        {/* 예시 질문 — 알약 대신 네 칸 카드. 역할 라벨(숫자·비교·추이·구조) + 질문 한 줄. 클릭 = 질문 실행.
            지금 보고 있는 질문은 선택 표시만 하고 다시 돌리지 않는다. */}
        <div className="mt-4 grid grid-cols-4 gap-2.5">
          {NL_CHIPS.map(({ kind, q }) => {
            const on = q === activeQ
            return (
              <button
                key={q}
                type="button"
                onClick={() => {
                  if (!on) ask(q)
                }}
                aria-pressed={on}
                className={`group flex min-w-0 flex-col items-start gap-1 rounded-lg border px-3.5 py-2.5 text-left transition-colors ${
                  on ? 'border-blue bg-row-sel' : 'cursor-pointer border-line bg-white hover:border-tint-blue-line hover:bg-soft'
                }`}
              >
                <span className={`text-[10.5px] font-semibold tracking-[.02em] ${on ? 'text-blue' : 'text-g2'}`}>{kind}</span>
                <span className={`break-keep text-[12.5px] leading-[1.4] ${on ? 'font-semibold text-navy' : 'text-ink'}`}>{q}</span>
              </button>
            )
          })}
        </div>
      </Card>

      {/* 파이프라인 트래커 — 질문 전에도 구조가 보이고, 질의 중엔 단계가 순서대로 켜진다 */}
      <div className="mt-4">
        <NlqPipeline phase={phase} def={def} rejected={rejected} />
      </div>

      {hasSql && (
        <div className="mt-[18px]">
          <NlqSqlPanel
            sqlText={sqlText}
            onSqlChange={setSqlText}
            editing={editing}
            onStartEdit={() => setEditing(true)}
            onCancelEdit={cancelEdit}
            onReverify={reverify}
            onRunEdited={runEdited}
            checks={validation?.checks}
            valid={validation?.valid}
            reason={validation?.reason}
            validating={validating}
            verifyNotice={verifyNotice}
          />
        </div>
      )}

      {/* 좌: 진행/결과 · 우: 최근 질의 (질문 전에도 이력 5건은 보인다) */}
      <div className="mt-[18px] flex items-start gap-5">
        <div className="flex min-w-0 flex-1 flex-col gap-[18px]">
          {phase === 'gen' && <PhaseCard label="SQL 생성 중…" note="1/2 단계 · LLM API 스키마 매핑" />}

          {phase === 'unknown' && (
            <EmptyState
              title="이 질문은 데모 mock에 준비되지 않았습니다"
              description="위의 예시 질문을 사용해 주세요. (실제 API 연결 시에는 모든 자연어 질문이 처리됩니다)"
            />
          )}

          {phase === 'rejected' && rejected && (
            <Card className="animate-[om-fadein_.25s] p-5" style={{ borderColor: 'var(--color-tint-red-line)' }}>
              <div className="flex flex-wrap items-center gap-2.5">
                <span className="flex h-[34px] w-[34px] flex-none items-center justify-center rounded-full bg-tint-red text-[19px] font-extrabold text-red">
                  !
                </span>
                <div className="text-base font-extrabold text-red">
                  실행할 수 없는 요청입니다 — {reasonText(rejected.reject_reason)}
                </div>
                <Badge variant="t-red" className="ml-auto">
                  {reasonCode(rejected.reject_reason)}
                </Badge>
              </div>
              {/* 서버가 준 reject_reason 을 그대로 인용한다 (문구 창작 금지) */}
              <div className="mt-3 rounded-lg border border-tint-red-line bg-row-red px-3.5 py-3 text-[13px] text-ink">
                요청 &quot;<span className="font-mono">{rejected.question ?? activeQ}</span>&quot; 은{' '}
                <span className="font-mono font-bold text-red">{rejected.reject_reason}</span> 사유로 SQL을
                실행하지 않았습니다.
              </div>
              <div className="mt-2.5 flex items-center gap-3 text-xs text-g1">
                <span>구문 오류는 1회 자동 교정을 시도하지만, 정책 위반은 즉시 거부됩니다</span>
                <span className="ml-auto font-mono">{(rejected.latency_ms ?? 0).toLocaleString()}ms</span>
              </div>
            </Card>
          )}

          {phase === 'run' && <PhaseCard label="쿼리 실행 중…" note="2/2 단계 · kosa_readonly" />}

          {phase === 'exec_error' && def && (
            <Card className="animate-[om-fadein_.25s] p-5">
              <div className="flex flex-wrap items-center gap-2.5">
                <div className="text-base font-extrabold text-navy">검증은 통과했으나 실행에 실패했습니다</div>
                <Badge variant="outline" className="ml-auto">DB_ERROR</Badge>
              </div>
              {/* 정책 거부(적색)와 구분되는 중립 스타일 — 서버 error_msg 를 그대로 인용 */}
              <div className="mt-3 rounded-lg border border-line bg-soft px-3.5 py-3 font-mono text-[13px] text-ink">
                {def.error_msg}
              </div>
              <div className="mt-2.5 text-right font-mono text-xs text-g1">{(def.latency_ms ?? 0).toLocaleString()}ms</div>
            </Card>
          )}

          {phase === 'failed' && (
            <EmptyState
              title="요청을 보내지 못했습니다"
              description="백엔드 서버(8010) 상태와 네트워크를 확인한 뒤 다시 시도해 주세요."
            />
          )}

          {phase === 'done' && (
            <NlqResultTabs
              def={def}
              tab={tab}
              onTab={setTab}
              sortDir={sortDir}
              onToggleSort={() => setSortDir((d) => (d === null ? 'desc' : d === 'desc' ? 'asc' : null))}
              sortKey={sortKey}
              rows={rows}
              footnote={footnote}
            />
          )}
        </div>

        <div className="flex w-[360px] flex-none flex-col gap-2.5">
          <div className="flex gap-2">
            <Button sm variant={sideTab === 'history' ? 'primary' : 'outline'} onClick={() => setSideTab('history')}>
              최근 질의
            </Button>
            <Button sm variant={sideTab === 'evaluation' ? 'primary' : 'outline'} onClick={() => setSideTab('evaluation')}>
              평가
            </Button>
          </div>
          {sideTab === 'history' ? (
            <NlqHistoryPanel
              items={history.slice((histPage - 1) * HIST_PAGE, histPage * HIST_PAGE)}
              activeQ={activeQ}
              onRerun={(q) => {
                if (q === activeQ) return // 이미 보고 있는 질의는 다시 돌리지 않는다
                ask(q)
              }}
              state={historyState}
              page={histPage}
              pageCount={Math.max(1, Math.ceil(history.length / HIST_PAGE))}
              onPage={setHistPage}
            />
          ) : (
            <NlqEvaluationPanel fetchEvaluations={getEvaluations} />
          )}
        </div>
      </div>
    </div>
  )
}

export default AnalyticsPage
