// 자연어 분석 — 디자인 v2 06 (읽기 전용 · 허용 테이블 16종)
import { useEffect, useMemo, useRef, useState } from 'react'
import { postQuery, validateSql } from '../../../shared/api/analytics.js'
import { NL_CHIPS, NL_INITIAL_HISTORY, NL_REJECT_REASONS } from '../mock/queries.js'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import Badge from '../../../shared/components/ui/Badge.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card } from '../../../shared/components/ui/Card.jsx'
import NlqSqlPanel from '../components/NlqSqlPanel.jsx'
import NlqResultTabs from '../components/NlqResultTabs.jsx'
import NlqHistoryPanel from '../components/NlqHistoryPanel.jsx'

// 0건 그룹 각주용 전체 챔버 목록 (설비 마스터 기준 4종)
const ALL_CHAMBERS = ['PHO-01-C1', 'PHO-01-C2', 'ETC-01-C1', 'ETC-01-C2']

const rejectReason = (code) => NL_REJECT_REASONS[code] ?? NL_REJECT_REASONS.REJECT_NON_SELECT

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
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[22px] font-extrabold text-navy">자연어 분석</div>
        <div className="text-xs text-g1">읽기 전용 · 허용 테이블 16종</div>
      </div>

      <Card className="px-5 py-[18px]">
        <div className="flex gap-3">
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') ask(e.target.value)
            }}
            placeholder="데이터에 대해 질문하세요 (예: 챔버별 알람 건수)"
            className="h-11 min-w-0 flex-1 rounded-lg border border-line bg-white px-3 font-mono text-sm text-ink focus:border-blue focus:outline-none"
          />
          <Button onClick={() => ask(question)} className="flex-none" style={{ height: 44, padding: '0 34px' }}>
            질문
          </Button>
        </div>
        {/* 예시 칩 — 클릭 = 질문 실행. '지워줘'(거부 유도)만 적색 틴트 */}
        <div className="mt-3 flex flex-wrap gap-2">
          {NL_CHIPS.map((q) => {
            const danger = q.includes('지워줘')
            return (
              <button
                key={q}
                type="button"
                onClick={() => ask(q)}
                className={`inline-flex h-7 cursor-pointer items-center rounded-full border px-4 font-sans text-[11.5px] font-semibold ${
                  danger ? 'border-tint-red-line bg-tint-red text-red' : 'border-tint-blue-line bg-tint-blue text-blue'
                }`}
              >
                {q}
              </button>
            )
          })}
        </div>
      </Card>

      {hasSql && (
        <div className="mt-[18px]">
          <NlqSqlPanel
            sqlText={sqlText}
            onSqlChange={setSqlText}
            editing={editing}
            onStartEdit={() => setEditing(true)}
            onCancelEdit={cancelEdit}
            onReverify={reverify}
            checks={checks}
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

          {phase === 'rejected' && (
            <Card className="animate-[om-fadein_.25s] p-5" style={{ borderColor: 'var(--color-tint-red-line)' }}>
              <div className="flex flex-wrap items-center gap-2.5">
                <span className="flex h-[34px] w-[34px] flex-none items-center justify-center rounded-full bg-tint-red text-[19px] font-extrabold text-red">
                  !
                </span>
                <div className="text-base font-extrabold text-red">
                  실행할 수 없는 요청입니다 — {rejectReason(rejectCode)}
                </div>
                <Badge variant="t-red" className="ml-auto">
                  {rejectCode}
                </Badge>
              </div>
              <div className="mt-3 rounded-lg border border-tint-red-line bg-row-red px-3.5 py-3 text-[13px] text-ink">
                요청 &quot;<span className="font-mono">{activeQ}</span>&quot; 은{' '}
                <span className="font-bold text-red">{rejectReason(rejectCode)}</span> 정책에 따라 SQL을 생성하지
                않았습니다.
              </div>
              <div className="mt-2.5 text-xs text-g1">
                구문 오류는 1회 자동 교정을 시도하지만, 정책 위반은 즉시 거부됩니다
              </div>
            </Card>
          )}

          {phase === 'run' && <PhaseCard label="쿼리 실행 중…" note="2/2 단계 · kosa_readonly" />}

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
        </div>

        <NlqHistoryPanel items={historyItems} activeQ={activeQ} onRerun={ask} />
      </div>
    </div>
  )
}

export default AnalyticsPage
