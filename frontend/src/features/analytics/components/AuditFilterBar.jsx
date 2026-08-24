import Button from '../../../shared/components/ui/Button.jsx'
import { FilterBar, FilterField, FilterSelect } from '../../../shared/components/ui/FilterField.jsx'
import { ALL } from './auditModel.js'

// 감사로그 필터 — 라이트 시안 7번. [조회] 버튼 없이 변경 즉시 재조회한다.
// 기간(date~date) + 유형(응답 event_types) + 주체(AGENT/HUMAN/SYSTEM) + 대상 검색 + 샘플 ID 칩 토글

const DATE_CLS = 'h-9 rounded-lg border border-field-line bg-white px-2.5 font-mono text-[12px] text-ink'

function AuditFilterBar({ eventTypes, value, onChange, samples, onReset }) {
  const set = (key, v) => onChange({ ...value, [key]: v })
  return (
    <div>
      <FilterBar className="pb-2">
        <FilterField label="기간">
          <span className="flex items-center gap-1.5">
            <input type="date" value={value.from} onChange={(e) => set('from', e.target.value)} className={DATE_CLS} />
            <span className="text-[11px] text-g2">~</span>
            <input type="date" value={value.to} onChange={(e) => set('to', e.target.value)} className={DATE_CLS} />
          </span>
        </FilterField>
        <FilterField label="이벤트 유형">
          <FilterSelect value={value.type} onChange={(v) => set('type', v)} options={[ALL, ...eventTypes]} mono minWidth={210} />
        </FilterField>
        <FilterField label="주체">
          <FilterSelect value={value.actor} onChange={(v) => set('actor', v)} options={[ALL, 'AGENT', 'HUMAN', 'SYSTEM']} mono minWidth={110} />
        </FilterField>
        <FilterField label="대상 (entity_id)">
          <input
            value={value.target}
            onChange={(e) => set('target', e.target.value)}
            placeholder="RUN- / ACT- / APR- …"
            className="h-9 w-[210px] rounded-lg border border-field-line bg-white px-3 font-mono text-[12px] text-ink placeholder:text-faint"
          />
        </FilterField>
        <Button variant="outline" onClick={onReset}>
          초기화
        </Button>
        <div className="ml-auto pb-2.5 text-xs text-g1">변경 즉시 적용</div>
      </FilterBar>
      {samples.length > 0 && (
        <div className="mb-3.5 flex items-center gap-2">
          <span className="text-[11px] font-bold text-g2">샘플 ID</span>
          {samples.map((id) => {
            const on = value.target === id
            return (
              <button
                key={id}
                type="button"
                onClick={() => set('target', on ? '' : id)}
                className={`inline-flex h-6 cursor-pointer items-center rounded-full border px-2.5 font-mono text-[10.5px] font-semibold ${
                  on ? 'border-blue bg-blue text-white' : 'border-tint-blue-line bg-tint-blue text-blue-hover'
                }`}
              >
                {id}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default AuditFilterBar
