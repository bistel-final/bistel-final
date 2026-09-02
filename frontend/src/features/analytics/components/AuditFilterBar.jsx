import { FilterBar, FilterField, FilterSelect } from '../../../shared/components/ui/FilterField.jsx'
import { ALL, actorLabel, eventLabel } from './auditModel.js'

// 감사로그 필터 — 화면 7. 조회 버튼 없이 변경 즉시 재조회한다.
// 기간(date~date) + 유형(응답 event_types) + 행위자(AGENT/HUMAN/SYSTEM) + 대상 ID 검색. 그 외 안내 문구는 두지 않는다.

// 입력 스킨은 공용 FilterSelect 와 같은 높이·테두리·글자 무게(h-9 · border-line · 13px semibold navy)로 맞춘다.
const FIELD = 'h-9 rounded-lg border border-line bg-white text-[13px] font-semibold text-navy'
const DATE_CLS = `${FIELD} w-[150px] px-2.5 font-mono`

// 라벨은 필드 안쪽 글자 시작선에 맞춰 살짝 뒤로 민다 (박스 테두리와 글자가 같은 x 에 서는 것보다 이쪽이 정렬되어 보인다).
const L = ({ children }) => <span className="pl-[3px]">{children}</span>

function AuditFilterBar({ eventTypes, value, onChange }) {
  const set = (key, v) => onChange({ ...value, [key]: v })
  return (
    <FilterBar className="pb-4">
      <FilterField label={<L>기간</L>}>
        <span className="flex h-9 items-center gap-1.5">
          <input type="date" value={value.from} onChange={(e) => set('from', e.target.value)} className={DATE_CLS} />
          <span className="text-[11px] text-g2">~</span>
          <input type="date" value={value.to} onChange={(e) => set('to', e.target.value)} className={DATE_CLS} />
        </span>
      </FilterField>
      <FilterField label={<L>이벤트 유형</L>}>
        <FilterSelect
          value={value.type}
          onChange={(v) => set('type', v)}
          options={[ALL, ...eventTypes.map((t) => ({ value: t, label: eventLabel(t) }))]}
          minWidth={190}
        />
      </FilterField>
      <FilterField label={<L>행위자</L>}>
        <FilterSelect
          value={value.actor}
          onChange={(v) => set('actor', v)}
          options={[ALL, ...['AGENT', 'HUMAN', 'SYSTEM'].map((a) => ({ value: a, label: actorLabel(a) }))]}
          minWidth={110}
        />
      </FilterField>
      <FilterField label={<L>대상 ID</L>}>
        <input
          value={value.target}
          onChange={(e) => set('target', e.target.value)}
          placeholder="알람 · 실행 · 조치 · 승인 ID"
          className={`${FIELD} w-[210px] px-3 font-mono placeholder:font-sans placeholder:font-normal placeholder:text-faint`}
        />
      </FilterField>
    </FilterBar>
  )
}

export default AuditFilterBar
