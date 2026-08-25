import Button from '../../../shared/components/ui/Button.jsx'
import { FilterBar, FilterField, FilterSelect } from '../../../shared/components/ui/FilterField.jsx'
import { ALL } from './scopeModel.js'

// 공통 필터바 — 기간(date~date) + AREA/설비/챔버 계단식(상위 변경 시 하위 초기화)
// draft/applied 분리: [조회] 버튼으로만 재조회한다 (라이트 시안 공통 스펙)

const DATE_CLS = 'h-9 rounded-lg border border-field-line bg-white px-2.5 font-mono text-[12px] text-ink'

function ScopeFilterBar({ hierarchy, draft, onDraft, onApply, onReset, note }) {
  const scoped = hierarchy.filter((h) => draft.area === ALL || h.area_id === draft.area)
  const areaOpts = [ALL, ...new Set(hierarchy.map((h) => h.area_id))]
  const eqpOpts = [ALL, ...scoped.map((h) => h.equipment_id)]
  const chOpts = [
    ALL,
    ...scoped.filter((h) => draft.equipment === ALL || h.equipment_id === draft.equipment).flatMap((h) => h.chambers),
  ]

  const set = (key, value) => {
    const next = { ...draft, [key]: value }
    if (key === 'area') {
      next.equipment = ALL
      next.chamber = ALL
    }
    if (key === 'equipment') next.chamber = ALL
    onDraft(next)
  }

  return (
    <FilterBar>
      <FilterField label="기간">
        <span className="flex items-center gap-1.5">
          <input type="date" value={draft.from} onChange={(e) => set('from', e.target.value)} className={DATE_CLS} />
          <span className="text-[11px] text-g2">~</span>
          <input type="date" value={draft.to} onChange={(e) => set('to', e.target.value)} className={DATE_CLS} />
        </span>
      </FilterField>
      <FilterField label="AREA">
        <FilterSelect value={draft.area} onChange={(v) => set('area', v)} options={areaOpts} minWidth={120} />
      </FilterField>
      <FilterField label="설비">
        <FilterSelect value={draft.equipment} onChange={(v) => set('equipment', v)} options={eqpOpts} minWidth={130} />
      </FilterField>
      <FilterField label="챔버">
        <FilterSelect value={draft.chamber} onChange={(v) => set('chamber', v)} options={chOpts} minWidth={140} />
      </FilterField>
      <Button onClick={onApply}>조회</Button>
      <Button variant="outline" onClick={onReset}>
        초기화
      </Button>
      {note && <div className="ml-auto pb-2.5 text-xs text-g1">{note}</div>}
    </FilterBar>
  )
}

export default ScopeFilterBar
