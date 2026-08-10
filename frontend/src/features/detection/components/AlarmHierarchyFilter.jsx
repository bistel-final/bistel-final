// 계층 필터바 — 기간(정적) · 공정(AREA) → 설비 → 챔버 → 파라미터
// 목록이 서버 페이지네이션으로 바뀌어(한 페이지 12건) 선택지를 알람에서 유도할 수 없다.
// 선택지는 GET /traces/catalog(areas · equipments[].chambers · sensors)에서 가져온다.
import {
  FilterBar,
  FilterField,
  FilterNote,
  FilterSelect,
  FilterStatic,
} from '../../../shared/components/ui/FilterField.jsx'

const ALL = '전체'

function AlarmHierarchyFilter({ catalog, value, onChange }) {
  const equipments = (catalog?.equipments ?? []).filter((e) => value.area === ALL || e.area_id === value.area)
  const chambers = equipments
    .filter((e) => value.equipment === ALL || e.equipment_id === value.equipment)
    .flatMap((e) => e.chambers ?? [])

  const levels = [
    { key: 'area', label: '공정', opts: (catalog?.areas ?? []).map((a) => a.area_id) },
    { key: 'equipment', label: '설비', opts: equipments.map((e) => e.equipment_id) },
    { key: 'chamber', label: '챔버', opts: [...new Set(chambers)].sort() },
    // TODO(api): catalog 에 챔버↔센서 매핑이 없다 — 파라미터 선택지는 전체 센서
    { key: 'sensor', label: '파라미터', opts: (catalog?.sensors ?? []).map((s) => s.sensor_id) },
  ]

  return (
    <FilterBar>
      <FilterField label="기간">
        {/* TODO(api): GET /alarms 에 기간 범위 응답 필드가 없다 — 날짜 필터는 명세 확정 후 */}
        <FilterStatic minWidth={190} mono>
          {ALL}
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
