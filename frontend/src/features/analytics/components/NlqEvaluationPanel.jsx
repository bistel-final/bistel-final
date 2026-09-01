// 평가 보조 탭 — GET /analytics/evaluations 실응답만 그린다 (V5-D-2.6, route-level Mock 0).
// 채점은 러너(run_analytics_eval.py)의 몫이고 여기는 immutable artifact 의 projection 을
// 읽기만 한다. 8번째 primary menu 가 아니라 /analytics 우측 패널의 탭이다.
// 응답 계약: EvaluationListResponse → items[EvaluationResponse{run_id, executed_at, provider,
// model, prompt_version, correct, total, accuracy, defense_passed, defense_total, items[...]}]
import { useEffect, useState } from 'react'
import Badge from '../../../shared/components/ui/Badge.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'

const fmtAt = (iso) => {
  const s = String(iso ?? '')
  return s ? s.slice(0, 16).replace('T', ' ') : '—'
}

function NlqEvaluationPanel({ fetchEvaluations }) {
  const [state, setState] = useState('loading') // loading | error | ready
  const [run, setRun] = useState(null)
  const [openFail, setOpenFail] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchEvaluations({ latest: true })
      .then((res) => {
        if (cancelled) return
        setRun(res?.items?.[0] ?? null)
        setState('ready')
      })
      .catch(() => {
        if (!cancelled) setState('error')
      })
    return () => {
      cancelled = true
    }
  }, [fetchEvaluations])

  return (
    <Card className="w-[360px] flex-none">
      <CardHeader title="Text2SQL 평가" note={run ? `최신 실행 · ${fmtAt(run.executed_at)}` : 'immutable artifact'} />
      <div className="flex flex-col gap-2.5 px-4 pb-4">
        {state === 'loading' && <div className="px-1 py-3 text-xs text-g2">평가 이력을 불러오는 중…</div>}
        {state === 'error' && (
          <div className="rounded-lg border border-line bg-soft px-3 py-2.5 text-xs text-g1">
            평가 이력을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.
          </div>
        )}
        {state === 'ready' && !run && (
          <div className="px-1 py-3 text-xs text-g2">
            평가 실행 기록이 없습니다. <span className="font-mono">run_analytics_eval.py</span> 실행 후 표시됩니다.
          </div>
        )}

        {state === 'ready' && run && (
          <>
            {/* 요약 — 정답/전체·정확도·실행 환경 */}
            <div className="rounded-lg border border-line bg-soft px-3.5 py-3">
              <div className="flex items-end justify-between">
                <div>
                  <div className="text-[11px] text-g2">정답 / 전체</div>
                  <div className="font-mono text-[26px] font-extrabold leading-none text-navy">
                    {run.correct}
                    <span className="text-[15px] font-bold text-g2"> / {run.total}</span>
                  </div>
                </div>
                <Badge variant={run.accuracy >= 0.7 ? 't-green' : 't-red'}>
                  정확도 {Math.round(run.accuracy * 100)}%
                </Badge>
              </div>
              <div className="mt-2.5 grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[10.5px] text-g1">
                <span>model</span>
                <span className="truncate text-right" title={run.model}>{run.model}</span>
                <span>prompt</span>
                <span className="truncate text-right" title={run.prompt_version}>{run.prompt_version}</span>
                <span>defense</span>
                <span className="text-right">
                  {run.defense_passed} / {run.defense_total}
                </span>
                <span>run</span>
                <span className="truncate text-right" title={run.run_id}>{run.run_id}</span>
              </div>
            </div>

            {/* 질문별 결과 */}
            <div className="flex flex-col gap-1.5">
              {run.items.map((it) => (
                <div
                  key={it.case_id}
                  className={`rounded-lg border px-3 py-2 ${it.passed ? 'border-line bg-white' : 'border-tint-red-line bg-tint-red cursor-pointer'}`}
                  onClick={!it.passed ? () => setOpenFail(openFail === it.case_id ? null : it.case_id) : undefined}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[11px] font-bold text-navy">{it.case_id}</span>
                    <Badge variant={it.passed ? 't-green' : 't-red'}>{it.passed ? 'PASS' : 'FAIL'}</Badge>
                    {it.case_type === 'DEFENSE' && <Badge variant="t-gray">방어</Badge>}
                    <span className="ml-auto font-mono text-[10.5px] text-g2">
                      {it.latency_ms != null ? `${it.latency_ms.toLocaleString()}ms` : ''}
                    </span>
                  </div>
                  <div className="mt-1 truncate text-[12px] text-g1" title={it.question ?? ''}>
                    {it.question ?? '—'}
                  </div>
                  {!it.passed && openFail === it.case_id && it.reason && (
                    <div className="mt-1.5 whitespace-pre-wrap font-mono text-[10.5px] text-red">{it.reason}</div>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </Card>
  )
}

export default NlqEvaluationPanel
