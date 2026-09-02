import { useState } from 'react'
import { askAgent } from '../../../shared/api/agent.js'
import Badge from '../../../shared/components/ui/Badge.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'

function RunContextAsk({ agentRunId }) {
  const [question, setQuestion] = useState('')
  const [state, setState] = useState({ phase: 'idle', response: null, error: null })

  const submit = () => {
    const normalized = question.trim()
    if (!normalized || state.phase === 'loading') return
    setState({ phase: 'loading', response: null, error: null })
    askAgent({ question: normalized, agent_run_id: agentRunId }).then(
      (response) => setState({ phase: 'success', response, error: null }),
      () => setState({ phase: 'error', response: null, error: '저장된 실행 근거로 답변을 만들지 못했습니다.' }),
    )
  }

  return (
    <Card data-testid="agent-run-context-ask">
      <CardHeader title="이 실행에 후속 질문" note="저장된 진단·citation을 먼저 사용하는 읽기 전용 질의" />
      <div className="px-5 pb-4">
        <div className="flex gap-2">
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') submit() }}
            placeholder="예: 우선 확인할 항목과 영향 확인 범위를 설명해 주세요"
            className="h-9 min-w-0 flex-1 rounded-lg border border-field-line bg-white px-3 text-[12px]"
          />
          <Button sm disabled={!question.trim() || state.phase === 'loading'} onClick={submit}>
            {state.phase === 'loading' ? '확인 중…' : '질문'}
          </Button>
        </div>
        {state.phase === 'error' && <div className="mt-3 text-[11.5px] font-bold text-red">{state.error}</div>}
        {state.response && (
          <div className="mt-3 rounded-lg border border-tint-blue-line bg-tint-blue px-4 py-3">
            <div className="text-[12.5px] font-extrabold text-navy">{state.response.title}</div>
            <div className="mt-1 whitespace-pre-wrap text-[12px] leading-6 text-ink">{state.response.answer}</div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {(state.response.evidence_items ?? []).map((item) => (
                <Badge key={item.source_id} variant="t-green">{item.type} · {item.source_id}</Badge>
              ))}
            </div>
            {(state.response.limitations ?? []).length > 0 && (
              <div className="mt-2 text-[10.5px] text-g2">한계: {state.response.limitations.join(' · ')}</div>
            )}
          </div>
        )}
      </div>
    </Card>
  )
}

export default RunContextAsk
