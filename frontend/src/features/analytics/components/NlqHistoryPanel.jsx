// 최근 질의 — 성공·거부를 모두 기록한다 (디자인 v2 06)
// 거부(정책 위반) 건은 사유만 표기하고 재실행 진입점을 두지 않는다.
// 성공 건은 카드 클릭으로 다시 질의한다 (시안에 버튼이 없어 카드 자체를 진입점으로 유지).
import Badge from '../../../shared/components/ui/Badge.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'

function NlqHistoryPanel({ items, activeQ, onRerun }) {
  return (
    <Card className="w-[360px] flex-none">
      <CardHeader title="최근 질의" note="성공 · 거부 모두" />
      <div className="flex flex-col gap-2.5 px-4 pb-4">
        {items.map((h) => (
          <div
            key={h.q}
            onClick={h.ok ? () => onRerun(h.q) : undefined}
            title={h.ok ? '클릭하면 다시 질의합니다' : undefined}
            className={`rounded-lg border p-3.5 ${
              h.ok ? 'cursor-pointer border-line bg-white' : 'border-tint-red-line bg-row-red'
            } ${h.q === activeQ ? 'shadow-[inset_0_0_0_1.5px_#2062A8]' : ''}`}
          >
            <div className="text-[12.5px] font-bold text-ink">{h.q}</div>
            <div className="mt-2.5 flex items-center gap-3">
              <Badge variant={h.ok ? 't-green' : 'bg-red'}>{h.ok ? '성공' : '거부'}</Badge>
              <span className="font-mono text-[11px] text-g1">{h.rows}행</span>
              {!h.ok && h.reason && <span className="font-mono text-[11px] font-bold text-red">{h.reason}</span>}
              <span className="ml-auto font-mono text-[11px] text-g2">{h.lat.toLocaleString()}ms</span>
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}

export default NlqHistoryPanel
