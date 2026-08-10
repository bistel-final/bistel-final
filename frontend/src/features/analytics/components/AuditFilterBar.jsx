// 감사로그 필터 — 조회 전용 (디자인 v2 07). 상태를 바꾸는 액션 없음.
import {
  FilterBar,
  FilterField,
  FilterNote,
  FilterSelect,
  FilterStatic,
} from '../../../shared/components/ui/FilterField.jsx'

const ACTORS = ['전체', 'AGENT', 'HUMAN', 'SYSTEM']

// 대표 생애주기로 바로 진입할 수 있는 예시 대상 ID
const TARGET_SAMPLES = ['APR-0001', 'ACT-0002', 'RUN-20260603-0005']

function AuditFilterBar({ eventTypes, eventType, onEventType, actor, onActor, target, onTarget }) {
  return (
    <FilterBar>
      <FilterField label="기간">
        <FilterStatic minWidth={190}>2026-06-01 ~ 06-04</FilterStatic>
      </FilterField>
      <FilterField label="이벤트">
        <FilterSelect value={eventType} onChange={onEventType} options={['전체', ...eventTypes]} />
      </FilterField>
      <FilterField label="주체">
        <FilterSelect value={actor} onChange={onActor} options={ACTORS} />
      </FilterField>
      <FilterField label="대상 ID">
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={target}
            onChange={(e) => onTarget(e.target.value)}
            placeholder="예: ACT-0002"
            aria-label="대상 ID 검색"
            className="h-9 w-[170px] rounded-lg border border-line bg-white px-3 font-mono text-[13px] font-semibold text-navy focus:border-blue focus:outline-none"
          />
          {TARGET_SAMPLES.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => onTarget(target === s ? '' : s)}
              aria-pressed={target === s}
              className={`inline-flex h-7 cursor-pointer items-center rounded-full border px-3 font-mono text-[11px] font-semibold ${
                target === s ? 'border-tint-blue-line bg-tint-blue text-blue' : 'border-line bg-white text-g1'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </FilterField>
      <FilterNote>대상 ID 로 한 건의 생애를 따라간다</FilterNote>
    </FilterBar>
  )
}

export default AuditFilterBar
