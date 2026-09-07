// 최근 질문 — 고유 질문 최대 20개, 4개씩 페이지. 성공은 조용히, 거부·오류만 색을 갖는다.
// 항목 필드는 {question, ok, reason, logged}. 거부 건은 사람 말 한 줄로 이유를 보이고 재실행 진입점을 두지 않는다.
// 성공 건은 카드 클릭으로 다시 질문한다. 지금 보고 있는 질문은 부모가 재실행을 막는다.
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'

// 서버 사유("POLICY_REJECTED: 조회 질문으로 판정되지 않아 SQL 을 …")는 시스템 문장이다.
// 관리자에게는 코드별 한 줄로 바꿔 보이고, 원문은 hover 로만 남긴다.
const REASON_BY_CODE = {
  POLICY_REJECTED: '조회할 수 없는 요청이에요',
  VALIDATION_FAILED: '없는 항목을 물었거나 조회 범위를 벗어났어요',
  DB_ERROR: '잠시 후 다시 시도해 주세요',
}
const reasonCode = (reason) => String(reason ?? '').split(':')[0].trim()
const reasonLine = (reason) => {
  const code = reasonCode(reason)
  if (REASON_BY_CODE[code]) return REASON_BY_CODE[code]
  const s = String(reason ?? '')
  // 존재하지 않는 컬럼 등 검증 실패 원문은 코드 접두어 없이 오기도 한다
  if (/존재하지 않는|allowlist|허용/.test(s)) return REASON_BY_CODE.VALIDATION_FAILED
  return '처리할 수 없는 질문이에요'
}

const PAGER_BTN =
  'flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-white text-[14px] text-g1 transition-colors hover:border-tint-blue-line hover:text-blue disabled:cursor-not-allowed disabled:opacity-40'

function NlqHistoryPanel({ items, activeQ, onRerun, state = 'ready', page = 1, pageCount = 1, onPage }) {
  return (
    <Card className="w-[360px] flex-none">
      <CardHeader title="최근 질문" />
      <div className="flex flex-col gap-2 px-4 pb-4">
        {state === 'loading' && items.length === 0 && <div className="px-1 py-3 text-xs text-g2">최근 질문을 불러오는 중…</div>}
        {state === 'error' && items.length === 0 && (
          <div className="rounded-lg border border-line bg-soft px-3 py-2.5 text-xs text-g1">
            최근 질문을 불러오지 못했어요. 질문은 정상적으로 할 수 있어요.
          </div>
        )}
        {state === 'ready' && items.length === 0 && (
          <div className="px-1 py-3 text-xs text-g2">아직 질문한 기록이 없어요. 첫 질문을 해보세요.</div>
        )}
        {items.map((h) => {
          const on = h.question === activeQ
          return (
            <div
              key={h.question}
              onClick={h.ok && !on ? () => onRerun(h.question) : undefined}
              title={h.ok && !on ? '클릭하면 다시 질문해요' : h.ok ? undefined : String(h.reason ?? '')}
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
              {!h.ok && <div className="mt-1.5 pl-[18px] text-[11.5px] leading-[1.45] text-fail">{reasonLine(h.reason)}</div>}
            </div>
          )
        })}
        {pageCount > 1 && onPage && (
          <div className="mt-1 flex items-center justify-between border-t border-cell-line pt-3">
            <button type="button" onClick={() => onPage(Math.max(1, page - 1))} disabled={page <= 1} className={PAGER_BTN} aria-label="이전 페이지">
              ‹
            </button>
            <span className="text-[12.5px] text-g1">
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
