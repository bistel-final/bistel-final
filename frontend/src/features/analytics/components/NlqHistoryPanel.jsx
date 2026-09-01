// 최근 질의 — 성공·거부를 모두 기록한다 (디자인 v2 06)
// 항목 필드는 {question, ok, row_count, latency_ms, reason} (명세 응답과 같은 이름).
// 거부(정책 위반) 건은 사유만 표기하고 재실행 진입점을 두지 않는다.
// 성공 건은 카드 클릭으로 다시 질의한다 (시안에 버튼이 없어 카드 자체를 진입점으로 유지).
import Badge from '../../../shared/components/ui/Badge.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'

// "POLICY_REJECTED: 사유" → 사유만 (접두어는 거부 배지가 이미 말해 준다)
const reasonText = (reason) => {
  const s = String(reason ?? '')
  return s.split(':').slice(1).join(':').trim() || s
}

function NlqHistoryPanel({ items, activeQ, onRerun, state = 'ready', page = 1, pageCount = 1, total = 0, onPage }) {
  return (
    <Card className="w-[360px] flex-none">
      <CardHeader
        title="최근 질의"
        note={total ? <span className="font-mono">전체 {total}건 · 성공·거부 모두</span> : '성공 · 거부 모두'}
      />
      <div className="flex flex-col gap-2.5 px-4 pb-4">
        {/* V5-D-2.6: 실 이력 hydrate 의 4상태 — Loading · Error · Empty · Success */}
        {state === 'loading' && items.length === 0 && (
          <div className="px-1 py-3 text-xs text-g2">질의 이력을 불러오는 중…</div>
        )}
        {state === 'error' && items.length === 0 && (
          <div className="rounded-lg border border-line bg-soft px-3 py-2.5 text-xs text-g1">
            이력을 불러오지 못했습니다. 이력 저장소가 미구성이거나 일시 장애일 수 있으며, 질의 기능은 정상 동작합니다.
          </div>
        )}
        {state === 'ready' && items.length === 0 && (
          <div className="px-1 py-3 text-xs text-g2">아직 질의 기록이 없습니다. 첫 질문을 던져 보세요.</div>
        )}
        {items.map((h) => (
          <div
            key={h.question}
            onClick={h.ok ? () => onRerun(h.question) : undefined}
            title={h.ok ? '클릭하면 다시 질의합니다' : undefined}
            className={`rounded-lg border p-3.5 ${
              h.ok ? 'cursor-pointer border-line bg-white' : 'border-tint-red-line bg-row-red'
            } ${h.question === activeQ ? 'shadow-[inset_0_0_0_1.5px_#2062A8]' : ''}`}
          >
            <div className="text-[12.5px] font-bold text-ink">{h.question}</div>
            <div className="mt-2.5 flex items-center gap-3">
              <Badge variant={h.ok ? 't-green' : 't-red'}>{h.ok ? '성공' : '거부'}</Badge>
              {h.logged === false && (
                <span title="이 질의는 이력 DB(nl_query_log)에 기록되지 않았습니다 — 기록 계정·DSN 설정을 확인하세요">
                  <Badge variant="t-amber">기록 안 됨</Badge>
                </span>
              )}
              <span className="font-mono text-[11px] text-g1">{h.row_count ?? 0}행</span>
              {!h.ok && h.reason && (
                <span className="min-w-0 flex-1 truncate font-mono text-[11px] font-bold text-red">
                  {reasonText(h.reason)}
                </span>
              )}
              <span className="ml-auto font-mono text-[11px] text-g2">
                {(h.latency_ms ?? 0).toLocaleString()}ms
              </span>
            </div>
          </div>
        ))}
        {/* 서버 pagination — 좁은 패널용 컴팩트 페이저 (번호 나열 대신 ‹ n / N ›) */}
        {pageCount > 1 && onPage && (
          <div className="mt-1 flex items-center justify-between border-t border-cell-line pt-3">
            <button
              type="button"
              onClick={() => onPage(Math.max(1, page - 1))}
              disabled={page <= 1}
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-white text-[14px] text-g1 transition-colors hover:border-tint-blue-line hover:text-blue disabled:cursor-not-allowed disabled:opacity-40"
              aria-label="이전 페이지"
            >
              ‹
            </button>
            <span className="font-mono text-[12.5px] text-g1">
              <span className="font-bold text-navy">{page}</span>
              <span className="mx-1 text-g2">/</span>
              {pageCount}
              <span className="ml-1.5 font-sans text-[11.5px] text-g2">페이지</span>
            </span>
            <button
              type="button"
              onClick={() => onPage(Math.min(pageCount, page + 1))}
              disabled={page >= pageCount}
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-white text-[14px] text-g1 transition-colors hover:border-tint-blue-line hover:text-blue disabled:cursor-not-allowed disabled:opacity-40"
              aria-label="다음 페이지"
            >
              ›
            </button>
          </div>
        )}
      </div>
    </Card>
  )
}

export default NlqHistoryPanel
