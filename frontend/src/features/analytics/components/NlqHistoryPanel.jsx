// 최근 질의 — 성공·거부를 모두 기록한다.
// 거부(정책 위반) 건은 사유만 표기하고 재생성 버튼을 노출하지 않는다.

function NlqHistoryPanel({ items, activeQ, onRerun }) {
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-white shadow-[0_1px_3px_rgba(15,42,92,.05)]">
      <div className="flex items-center gap-2 border-b border-line bg-page px-[18px] py-3">
        <span className="text-sm font-extrabold text-navy">최근 질의</span>
        <span className="ml-auto font-mono text-[11.5px] font-bold text-slate-light">{items.length}건</span>
      </div>
      {items.map((h) => (
        <div
          key={h.q}
          className="flex flex-col gap-1.5 border-b border-line-soft px-[18px] py-3"
          style={h.q === activeQ ? { background: '#F7FAFF' } : undefined}
        >
          <div className="flex items-start gap-2">
            <span
              className="mt-[1px] flex-none rounded-md px-2 py-[3px] text-[11px] font-extrabold"
              style={h.ok ? { background: '#DCFCE7', color: '#16A34A' } : { background: '#FEE2E2', color: '#DC2626' }}
            >
              {h.ok ? '성공' : '거부'}
            </span>
            <span className="min-w-0 flex-1 break-words text-[13.5px] font-semibold text-ink">{h.q}</span>
          </div>
          {!h.ok && h.reason && (
            <div className="rounded-md bg-[#FEF2F2] px-2.5 py-[5px] text-[12px] font-bold text-oos">
              사유 · {h.reason}
            </div>
          )}
          <div className="flex items-center gap-2">
            <span className="font-mono text-[11.5px] font-bold text-slate-light">
              row_cnt {h.rows} · {h.lat.toLocaleString()}ms
            </span>
            {h.ok && (
              <button
                onClick={() => onRerun(h.q)}
                className="ml-auto cursor-pointer rounded-[7px] border border-[#BFDBFE] bg-white px-2.5 py-[5px] font-sans text-[11.5px] font-extrabold text-brand hover:bg-[#F0F6FF]"
              >
                재생성
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

export default NlqHistoryPanel
