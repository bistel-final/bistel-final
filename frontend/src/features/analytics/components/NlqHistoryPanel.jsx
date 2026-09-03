// 최근 질의 — 고유 질문 최대 20개, 4개씩 페이지. 성공은 조용히, 거부·오류만 색을 갖는다.
// 항목 필드는 {question, ok, reason, logged}. 거부 건은 사유만 표기하고 재실행 진입점을 두지 않는다.
// 성공 건은 카드 클릭으로 다시 질의한다. 지금 보고 있는 질의는 부모가 재실행을 막는다.
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'

// "POLICY_REJECTED: 사유" → 사유만 (접두어는 색이 이미 말해 준다)
const reasonText = (reason) => {
  const s = String(reason ?? '')
  return s.split(':').slice(1).join(':').trim() || s
}

const PAGER_BTN =
  'flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-white text-[14px] text-g1 transition-colors hover:border-tint-blue-line hover:text-blue disabled:cursor-not-allowed disabled:opacity-40'

function NlqHistoryPanel({ items, activeQ, onRerun, state = 'ready', page = 1, pageCount = 1, onPage }) {
  return (
    <Card className="w-[360px] flex-none">
      <CardHeader title="최근 질의" />
      <div className="flex flex-col gap-2 px-4 pb-4">
        {state === 'loading' && items.length === 0 && <div className="px-1 py-3 text-xs text-g2">질의 이력을 불러오는 중…</div>}
        {state === 'error' && items.length === 0 && (
          <div className="rounded-lg border border-line bg-soft px-3 py-2.5 text-xs text-g1">
            이력을 불러오지 못했습니다. 질의 기능은 정상 동작합니다.
          </div>
        )}
        {state === 'ready' && items.length === 0 && (
          <div className="px-1 py-3 text-xs text-g2">아직 질의 기록이 없습니다. 첫 질문을 던져 보세요.</div>
        )}
        {items.map((h) => {
          const on = h.question === activeQ
          return (
            <div
              key={h.question}
              onClick={h.ok && !on ? () => onRerun(h.question) : undefined}
              title={h.ok && !on ? '클릭하면 다시 질의합니다' : undefined}
              aria-current={on ? 'true' : undefined}
              className={`rounded-lg border p-3 transition-colors ${
                on
                  ? 'border-blue bg-row-sel'
                  : h.ok
                    ? 'cursor-pointer border-line bg-white hover:bg-soft'
                    : 'border-line bg-white'
              }`}
            >
              <div className={`flex items-start gap-2.5 text-[12.5px] leading-[1.45] ${on ? 'font-semibold text-navy' : 'text-ink'}`}>
                <span
                  className="mt-[6px] h-2 w-2 flex-none rounded-full"
                  style={{ background: h.ok ? 'var(--color-navy-2)' : 'var(--color-fail)' }}
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1 break-keep">{h.question}</span>
              </div>
              {!h.ok && h.reason && (
                <div className="mt-1.5 line-clamp-2 pl-[18px] text-[11.5px] leading-[1.45] text-fail" title={reasonText(h.reason)}>{reasonText(h.reason)}</div>
              )}
              {h.logged === false && (
                <div className="mt-1.5 pl-[18px] text-[11px] text-g2" title="이 질의는 이력 DB에 기록되지 않았습니다 — 기록 계정·DSN 설정을 확인하세요">
                  이력에 기록되지 않음
                </div>
              )}
            </div>
          )
        })}
        {pageCount > 1 && onPage && (
          <div className="mt-1 flex items-center justify-between border-t border-cell-line pt-3">
            <button type="button" onClick={() => onPage(Math.max(1, page - 1))} disabled={page <= 1} className={PAGER_BTN} aria-label="이전 페이지">
              ‹
            </button>
            <span className="font-mono text-[12.5px] text-g1">
              <span className="font-bold text-navy">{page}</span>
              <span className="mx-1 text-g2">/</span>
              {pageCount}
            </span>
            <button type="button" onClick={() => onPage(Math.min(pageCount, page + 1))} disabled={page >= pageCount} className={PAGER_BTN} aria-label="다음 페이지">
              ›
            </button>
          </div>
        )}
      </div>
    </Card>
  )
}

export default NlqHistoryPanel
