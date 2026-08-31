// 파이프라인 트래커 (발표 재설계) — "LLM 은 생성만, 판단은 전부 규칙" 을 화면 한 줄로 보여준다.
// 질문 → 계획(LLM) → 검증(규칙 6종) → 실행(readonly) → 교차확인(Neo4j)
// 질의 중엔 단계가 순서대로 켜지고, 끝나면 LLM 한 칸·규칙 세 칸이 남는다 — 발표의 30초 장면.
// 상태는 AnalyticsPage 의 phase·응답만으로 결정론적으로 파생한다 (별도 API 없음).
import { Card } from '../../../shared/components/ui/Card.jsx'

const STEPS = [
  { key: 'ask', label: '질문', sub: '자연어' },
  { key: 'plan', label: '계획', sub: 'gpt-4o-mini', tag: 'LLM' },
  { key: 'verify', label: '검증', sub: '규칙 6종 · sqlglot', tag: '규칙' },
  { key: 'run', label: '실행', sub: 'kosa_readonly · LIMIT 500', tag: '규칙' },
  { key: 'cross', label: '교차확인', sub: 'PostgreSQL ↔ Neo4j', tag: '규칙' },
]

const LLM_REFUSED = 'POLICY_REJECTED: 조회 질문으로 판정되지'

// phase·응답 → 단계별 상태: pending | active | done | fail | skip
export function deriveSteps({ phase, def, rejected }) {
  const s = { ask: 'pending', plan: 'pending', verify: 'pending', run: 'pending', cross: 'pending' }
  if (!phase) return s
  s.ask = 'done'
  if (phase === 'gen') s.plan = 'active'
  if (phase === 'unknown' || phase === 'failed') s.plan = 'fail'
  if (phase === 'rejected') {
    const reason = rejected?.reject_reason ?? ''
    if (reason.startsWith(LLM_REFUSED)) {
      s.plan = 'fail'
    } else {
      s.plan = 'done'
      s.verify = 'fail'
    }
  }
  if (phase === 'run' || phase === 'exec_error' || phase === 'done') {
    s.plan = 'done'
    s.verify = 'done'
  }
  if (phase === 'run') s.run = 'active'
  if (phase === 'exec_error') s.run = 'fail'
  if (phase === 'done') {
    s.run = 'done'
    const cc = def?.cross_check?.status
    s.cross = cc === 'MATCH' ? 'done' : cc === 'MISMATCH' ? 'fail' : 'skip'
  }
  return s
}

const NODE = {
  pending: 'border-line bg-white text-g2',
  active: 'border-blue bg-tint-blue text-blue',
  done: 'border-navy bg-navy text-white',
  fail: 'border-red bg-red text-white',
  skip: 'border-dashed border-line bg-white text-faint',
}
const LABEL = {
  pending: 'text-g2',
  active: 'text-blue',
  done: 'text-navy',
  fail: 'text-red',
  skip: 'text-faint',
}

function Node({ index, state }) {
  if (state === 'active')
    return (
      <span className={`flex h-8 w-8 flex-none items-center justify-center rounded-full border-2 ${NODE.active}`}>
        <span className="h-3.5 w-3.5 animate-[om-spin_.8s_linear_infinite] rounded-full border-2 border-tint-blue-line border-t-blue" />
      </span>
    )
  return (
    <span className={`flex h-8 w-8 flex-none items-center justify-center rounded-full border-2 font-mono text-[12px] font-bold ${NODE[state]}`}>
      {state === 'done' ? '✓' : state === 'fail' ? '✕' : state === 'skip' ? '–' : index + 1}
    </span>
  )
}

function NlqPipeline({ phase, def, rejected }) {
  const states = deriveSteps({ phase, def, rejected })
  const crossSub = (() => {
    const cc = def?.cross_check
    if (!cc || phase !== 'done') return STEPS[4].sub
    if (cc.status === 'MATCH') return `두 저장소 일치 · ${cc.summary ?? ''}`
    if (cc.status === 'MISMATCH') return `불일치 · ${cc.summary ?? ''}`
    return '구조 질의 아님 · 생략'
  })()
  return (
    <Card className="px-6 py-4">
      <div className="flex items-center">
        {STEPS.map((step, i) => {
          const state = states[step.key]
          const sub = step.key === 'cross' ? crossSub : step.sub
          return (
            <div key={step.key} className="flex min-w-0 flex-1 items-center">
              <div className="flex min-w-0 items-center gap-3">
                <Node index={i} state={state} />
                <div className="min-w-0">
                  <div className={`flex items-center gap-1.5 text-[13.5px] font-bold leading-tight ${LABEL[state]}`}>
                    {step.label}
                    {step.tag && (
                      <span
                        className={`rounded-[4px] px-1.5 py-[1px] font-mono text-[9.5px] font-bold tracking-[.04em] ${
                          step.tag === 'LLM' ? 'bg-tint-blue text-blue' : 'bg-tint-navy text-navy'
                        }`}
                      >
                        {step.tag}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 truncate font-mono text-[11px] text-g2">{sub}</div>
                </div>
              </div>
              {i < STEPS.length - 1 && (
                <div
                  className={`mx-4 h-[2px] min-w-6 flex-1 rounded-full ${
                    states[STEPS[i + 1].key] === 'pending' || states[STEPS[i + 1].key] === 'skip' ? 'bg-cell-line' : 'bg-navy'
                  }`}
                />
              )}
            </div>
          )
        })}
      </div>
    </Card>
  )
}

export default NlqPipeline
