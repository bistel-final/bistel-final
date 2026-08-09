// 이벤트 유형별 집계 바 — 9종 전부 표시하고 0건도 회색 바로 남긴다 (숨기지 않음)
// 집계 기준은 현재 화면 slice가 아니라 "같은 필터 전체 집합"이다.
const barColor = (name, cnt) =>
  cnt === 0 ? '#CBD5E1' : name.includes('FAILED') || name.includes('REJECTED') ? '#DC2626' : '#1E5FC2'

function AuditEventTypeBars({ eventTypes, counts, scopeLabel }) {
  const max = Math.max(1, ...eventTypes.map((t) => counts[t] ?? 0))
  return (
    <div className="rounded-xl border border-line bg-white px-[18px] py-[17px] shadow-[0_1px_3px_rgba(15,42,92,.05)]">
      <div className="text-sm font-extrabold text-navy">이벤트 유형별 집계</div>
      <div className="mb-[13px] mt-[3px] text-[12px] font-semibold text-slate-light">{scopeLabel}</div>
      <div className="flex flex-col gap-[9px]">
        {eventTypes.map((t) => {
          const cnt = counts[t] ?? 0
          return (
            <div key={t}>
              <div className="flex items-center gap-1.5 font-mono text-[11px] font-extrabold">
                <span className={cnt === 0 ? 'text-slate-light' : 'text-slate'}>{t}</span>
                <span className={`ml-auto text-[12.5px] ${cnt === 0 ? 'text-slate-light' : 'text-navy'}`}>{cnt}</span>
              </div>
              <div className="mt-[3px] h-2 overflow-hidden rounded bg-line-soft">
                {/* 0건도 회색 스텁으로 존재를 남긴다 */}
                <div
                  className="h-full rounded transition-[width] duration-200"
                  style={{
                    width: cnt === 0 ? '10px' : `${Math.max(6, (cnt / max) * 100)}%`,
                    background: barColor(t, cnt),
                  }}
                />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default AuditEventTypeBars
