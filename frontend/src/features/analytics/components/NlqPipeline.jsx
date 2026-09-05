// 파이프라인 트래커 — "AI 는 질의 생성만, 판단은 전부 규칙" 을 화면 한 줄로 보여준다.
// 질문 → 질의 생성 → 안전 검증 → 결과 조회 → 교차확인. 단계 이름은 모두 동작 명사로 같은 성격을 유지한다.
// AI 가 관여하는 단계는 로딩 카드("SQL 생성 중")와 발표 설명으로 전달한다 — 배지·기술 설명을 단계에 붙이지 않는다.
// 교차확인만 끝난 뒤 결과 문구(일치·불일치·해당 없음)를 보인다 — 그게 그 단계의 답이다.
// 상태는 AnalyticsPage 의 phase·응답만으로 결정론적으로 파생한다 (별도 API 없음).
import { Card } from '../../../shared/components/ui/Card.jsx'

const STEPS = [
  { key: 'ask', label: '질문' },
  { key: 'plan', label: '질의 생성' },
  { key: 'verify', label: '안전 검증' },
  { key: 'run', label: '결과 조회' },
  { key: 'cross', label: '교차확인' },
]

const LLM_REFUSED = 'POLICY_REJECTED: 조회 질문으로 판정되지'

// phase·응답 → 단계별 상태: pending | active | done | fail | skip (컴포넌트 내부 전용 — fast refresh 규칙)
function deriveSteps({ phase, def, rejected }) {
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
  fail: 'border-fail bg-fail text-white',
  skip: 'border-dashed border-line bg-white text-faint',
}
const LABEL = {
  pending: 'text-g2',
  active: 'text-blue',
  done: 'text-navy',
  fail: 'text-fail',
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
  // 5단계 밑 문구 — 예외에만. 일치는 ✓ 가 이미 말하므로 비우고, 불일치(경고)·해당 없음(안 돌았음)만 한 단어로.
  const crossSub = (() => {
    const cc = def?.cross_check
    if (!cc || phase !== 'done') return null
    if (cc.status === 'MATCH') return null
    if (cc.status === 'MISMATCH') return '불일치'
    return '해당 없음'
  })()
  return (
    <Card className="px-6 py-4">
      <div className="flex items-center">
        {STEPS.map((step, i) => {
          const state = states[step.key]
          const sub = step.key === 'cross' ? crossSub : null
          return (
            <div key={step.key} className="flex min-w-0 flex-1 items-center">
              <div className="flex min-w-0 items-center gap-3">
                <Node index={i} state={state} />
                <div className="min-w-0">
                  <div className={`text-[13.5px] font-bold leading-tight ${LABEL[state]}`}>{step.label}</div>
                  {sub && <div className={`mt-0.5 truncate text-[11px] ${state === 'fail' ? 'text-fail' : 'text-g2'}`}>{sub}</div>}
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
