// 근거 카드 공통 껍데기 — 모든 카드는 하단에 「읽는 법」 한 줄을 반드시 갖는다
function RunEvidenceCard({ index, title, meta, children, reading }) {
  return (
    <div className="rounded-xl border border-line bg-white px-[18px] py-4 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="flex h-[20px] w-[20px] flex-none items-center justify-center rounded-md bg-line-soft font-mono text-[11px] font-extrabold text-brand">
          {index}
        </span>
        <span className="text-sm font-extrabold text-navy">{title}</span>
        {meta && <span className="ml-auto font-mono text-[11.5px] font-bold text-slate-light">{meta}</span>}
      </div>
      {children}
      <div className="mt-3 flex items-start gap-2 rounded-[10px] border border-line-soft bg-page px-3 py-2">
        <span className="mt-px flex-none text-[11px] font-extrabold text-slate-light">읽는 법</span>
        <span className="text-[12.5px] font-semibold leading-[1.5] text-ink">{reading}</span>
      </div>
    </div>
  )
}

// 실측이 없는 조합은 값을 만들어내지 않고 이 카드로 대체한다
export function RunNoData({ label, note }) {
  return (
    <div className="flex min-h-[132px] flex-col items-center justify-center gap-1.5 rounded-[10px] border-2 border-dashed border-line-input bg-page px-5 py-6 text-center">
      <div className="font-mono text-[13px] font-extrabold text-navy">{label}</div>
      <div className="text-[12.5px] font-bold text-slate">실측 미제공</div>
      {note && <div className="mt-0.5 text-[11.5px] font-semibold leading-[1.5] text-slate-light">{note}</div>}
    </div>
  )
}

export default RunEvidenceCard
