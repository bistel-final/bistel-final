import { useCallback, useState } from 'react'
import { getAuditLogsCore } from '../../api/analytics.js'
import { fmtDateTime } from '../../api/format.js'
import EmptyState from '../EmptyState.jsx'
import LoadingState from '../LoadingState.jsx'
import Button from '../ui/Button.jsx'
import { Card, CardHeader } from '../ui/Card.jsx'
import { auditTargetsOf, mergeAuditItems } from './run-audit-view-state.js'

function AuditRows({ items }) {
  return (
    <div className="divide-y divide-cell-line px-4 pb-3">
      {items.map((item) => (
        <div key={item.audit_id} className="grid grid-cols-[150px_1fr_auto] gap-4 py-3 text-xs">
          <span className="font-mono text-g2">{fmtDateTime(item.occurred_at)}</span>
          <span>
            <span className="font-bold text-ink">{item.event_type}</span>
            <span className="ml-2 font-mono text-g1">
              {item.entity_type} · {item.entity_id}
            </span>
          </span>
          <span className="font-mono text-g2">{item.actor_id ?? item.actor_type}</span>
        </div>
      ))}
    </div>
  )
}

export default function RunAuditSubview({ agent_run_id, action_id = null, approval_id = null }) {
  const [open, setOpen] = useState(false)
  const [state, setState] = useState({ phase: 'idle', items: [], error: null })

  const load = useCallback(() => {
    const targets = auditTargetsOf({ agent_run_id, action_id, approval_id })
    setState({ phase: 'loading', items: [], error: null })
    Promise.all(
      targets.map(([entity_type, entity_id]) => getAuditLogsCore({ entity_type, entity_id })),
    ).then(
      (groups) => setState({ phase: 'success', items: mergeAuditItems(groups), error: null }),
      () =>
        setState({
          phase: 'error',
          items: [],
          error: '감사 이력만 불러오지 못했습니다. Agent 상세에는 영향이 없습니다.',
        }),
    )
  }, [action_id, agent_run_id, approval_id])

  return (
    <Card>
      <CardHeader title="실행 감사 이력" note="실행 · 조치 · 승인 범위" />
      <div className="px-4 pb-4">
        {!open ? (
          <Button
            variant="outline"
            onClick={() => {
              setOpen(true)
              load()
            }}
          >
            감사 이력 보기
          </Button>
        ) : state.phase === 'loading' ? (
          <LoadingState message="감사 이력을 불러오는 중…" />
        ) : state.phase === 'error' ? (
          <div className="rounded-lg border border-tint-red-line bg-row-red p-4 text-xs text-red">
            {state.error}
            <div className="mt-3">
              <Button sm variant="outline-red" onClick={load}>
                다시 시도
              </Button>
            </div>
          </div>
        ) : state.items.length === 0 ? (
          <EmptyState title="이 실행의 감사 이력이 없습니다" />
        ) : (
          <AuditRows items={state.items} />
        )}
      </div>
    </Card>
  )
}
