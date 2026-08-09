// 계층 필터바 — 공정(AREA) → 설비 → 챔버 → 파라미터
// 계층은 알람 데이터에서 유도한다 (PHO-01-C1 → PHO-01 → PHOTO / ETC-* → ETCH)
const ALL = '전체'

const uniq = (rows, key) => [...new Set(rows.map((r) => r[key]).filter(Boolean))].sort()

function AlarmHierarchyFilter({ alarms, value, onChange, areaOf, resultCnt, totalCnt }) {
  const byArea = value.area === ALL ? alarms : alarms.filter((a) => areaOf(a) === value.area)
  const byEqp = value.equipment === ALL ? byArea : byArea.filter((a) => a.equipment_id === value.equipment)
  const byCh = value.chamber === ALL ? byEqp : byEqp.filter((a) => a.chamber_id === value.chamber)

  const levels = [
    {
      key: 'area',
      label: '공정',
      opts: [...new Set(alarms.map(areaOf))].sort(),
    },
    { key: 'equipment', label: '설비', opts: uniq(byArea, 'equipment_id') },
    { key: 'chamber', label: '챔버', opts: uniq(byEqp, 'chamber_id') },
    { key: 'sensor', label: '파라미터', opts: uniq(byCh, 'sensor_id') },
  ]

  return (
    <div className="flex flex-wrap items-center gap-x-[18px] gap-y-2.5 rounded-xl border border-line bg-white px-[18px] py-3.5 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
      {levels.map((lv, i) => (
        <div key={lv.key} className="flex items-center gap-2">
          {i > 0 && <span className="mr-1 text-[13px] font-bold text-line-input">›</span>}
          <span className="text-[13px] font-bold text-slate">{lv.label}</span>
          <select
            value={value[lv.key]}
            onChange={(e) => onChange(lv.key, e.target.value)}
            className="rounded-lg border border-line-input bg-white px-2.5 py-[7px] font-mono text-[13.5px] font-semibold text-navy"
          >
            {[ALL, ...lv.opts].map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange('reset')}
        className="cursor-pointer rounded-lg border border-line-input bg-white px-3 py-[7px] text-[13px] font-bold text-slate hover:bg-line-soft"
      >
        초기화
      </button>
      <span className="ml-auto text-[13px] font-bold text-slate">
        <span className="font-mono font-extrabold text-navy">{resultCnt}</span> / {totalCnt}건
      </span>
    </div>
  )
}

export default AlarmHierarchyFilter
