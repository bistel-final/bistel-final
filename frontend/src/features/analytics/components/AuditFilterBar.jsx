// 감사로그 필터 바 — 조회 전용 (상태를 바꾸는 액션 없음)
const ACTORS = ['전체', 'AGENT', 'HUMAN', 'SYSTEM']

// 대표 시나리오(ACT-0002 생애주기)로 바로 진입할 수 있는 예시 대상 ID
const TARGET_SAMPLES = ['ACT-0002', 'RUN-20260603-0005', 'APR-0005']

const chipStyle = (on) =>
  on
    ? { background: '#1E5FC2', color: '#FFFFFF', borderColor: '#1E5FC2' }
    : { background: '#FFFFFF', color: '#475569', borderColor: '#CBD5E1' }

function AuditFilterBar({
  eventTypes,
  events,
  onToggleEvent,
  actor,
  onActor,
  from,
  onFrom,
  to,
  onTo,
  target,
  onTarget,
  sort,
  onSort,
  onReset,
  activeCount,
}) {
  return (
    <div className="flex flex-col gap-[11px] rounded-xl border border-line bg-white px-[18px] py-3.5 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
      <div className="flex flex-wrap items-center gap-[7px]">
        <span className="mr-[3px] text-[13px] font-bold text-slate">이벤트</span>
        {eventTypes.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => onToggleEvent(t)}
            aria-pressed={events.includes(t)}
            className="cursor-pointer rounded-full border px-[11px] py-[5px] font-mono text-[11.5px] font-extrabold"
            style={chipStyle(events.includes(t))}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-bold text-slate">주체</span>
          <div className="flex overflow-hidden rounded-lg border border-line-input bg-white">
            {ACTORS.map((a) => (
              <button
                key={a}
                type="button"
                onClick={() => onActor(a)}
                className="cursor-pointer px-[13px] py-[7px] text-[13px] font-bold"
                style={
                  actor === a
                    ? { background: '#1E5FC2', color: '#FFFFFF' }
                    : { background: '#FFFFFF', color: '#475569' }
                }
              >
                {a}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-bold text-slate">기간</span>
          <input
            type="date"
            value={from}
            onChange={(e) => onFrom(e.target.value)}
            aria-label="조회 시작일"
            className="rounded-lg border border-line-input px-2.5 py-[7px] font-mono text-[13px] font-semibold text-navy"
          />
          <span className="font-bold text-[#94A3B8]">~</span>
          <input
            type="date"
            value={to}
            onChange={(e) => onTo(e.target.value)}
            aria-label="조회 종료일"
            className="rounded-lg border border-line-input px-2.5 py-[7px] font-mono text-[13px] font-semibold text-navy"
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-bold text-slate">정렬</span>
          <div className="flex overflow-hidden rounded-lg border border-line-input bg-white">
            {[
              { k: 'asc', l: '시간순' },
              { k: 'desc', l: '최신순' },
            ].map((o) => (
              <button
                key={o.k}
                type="button"
                onClick={() => onSort(o.k)}
                className="cursor-pointer px-[13px] py-[7px] text-[13px] font-bold"
                style={
                  sort === o.k
                    ? { background: '#0F2A5C', color: '#FFFFFF' }
                    : { background: '#FFFFFF', color: '#475569' }
                }
              >
                {o.l}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t border-line-soft pt-[11px]">
        <span className="text-[13px] font-bold text-slate">대상 ID</span>
        <input
          type="text"
          value={target}
          onChange={(e) => onTarget(e.target.value)}
          placeholder="엔터티 ID 부분일치 (예: ACT-0002)"
          aria-label="대상 ID 검색"
          className="w-[250px] rounded-lg border border-line-input px-3 py-2 font-mono text-[13px] font-semibold text-navy"
        />
        <span className="text-[12px] font-semibold text-slate-light">예시</span>
        {TARGET_SAMPLES.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onTarget(target === s ? '' : s)}
            aria-pressed={target === s}
            className="cursor-pointer rounded-full border px-[11px] py-[5px] font-mono text-[11.5px] font-extrabold"
            style={chipStyle(target === s)}
          >
            {s}
          </button>
        ))}
        <span className="ml-auto text-[12.5px] font-bold text-slate">
          필터 결과 <span className="font-mono text-navy">{activeCount}</span>건
        </span>
        <button
          type="button"
          onClick={onReset}
          className="cursor-pointer rounded-lg border border-line-input bg-white px-3 py-[6px] text-[12.5px] font-bold text-slate hover:bg-line-soft"
        >
          필터 초기화
        </button>
      </div>
    </div>
  )
}

export default AuditFilterBar
