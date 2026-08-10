// 계층 필터바 — 기간(정적) · 공정(AREA) → 설비 → 챔버 → 파라미터
// 계층은 알람 데이터에서 유도한다 (PHO-01-C1 → PHO-01 → PHOTO / ETC-* → ETCH)
import {
  FilterBar,
  FilterField,
  FilterNote,
  FilterSelect,
  FilterStatic,
} from '../../../shared/components/ui/FilterField.jsx'

const ALL = '전체'

const uniq = (rows, key) => [...new Set(rows.map((r) => r[key]).filter(Boolean))].sort()

function AlarmHierarchyFilter({ alarms, value, onChange, areaOf }) {
  const byArea = value.area === ALL ? alarms : alarms.filter((a) => areaOf(a) === value.area)
  const byEqp = value.equipment === ALL ? byArea : byArea.filter((a) => a.equipment_id === value.equipment)
  const byCh = value.chamber === ALL ? byEqp : byEqp.filter((a) => a.chamber_id === value.chamber)

  // 기간은 아직 정적 — 실제 데이터의 발생일 범위를 표시만 한다
  const days = alarms
    .map((a) => (a.occurred_at ?? '').slice(0, 10))
    .filter(Boolean)
    .sort()
  const period = days.length ? `${days[0]} ~ ${days[days.length - 1].slice(5)}` : '—'

  const levels = [
    { key: 'area', label: '공정', opts: [...new Set(alarms.map(areaOf))].sort() },
    { key: 'equipment', label: '설비', opts: uniq(byArea, 'equipment_id') },
    { key: 'chamber', label: '챔버', opts: uniq(byEqp, 'chamber_id') },
    { key: 'sensor', label: '파라미터', opts: uniq(byCh, 'sensor_id') },
  ]

  return (
    <FilterBar>
      <FilterField label="기간">
        <FilterStatic minWidth={190} mono>
          {period}
        </FilterStatic>
      </FilterField>
      {levels.map((lv) => (
        <FilterField key={lv.key} label={lv.label}>
          <FilterSelect value={value[lv.key]} onChange={(v) => onChange(lv.key, v)} options={[ALL, ...lv.opts]} />
        </FilterField>
      ))}
      <FilterNote>윗단을 고르면 아랫단 선택지가 좁아진다</FilterNote>
    </FilterBar>
  )
}

export default AlarmHierarchyFilter
