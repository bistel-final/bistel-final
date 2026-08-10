import { useState } from 'react'
import Button from '../../../shared/components/ui/Button.jsx'
import { FilterField, FilterSelect } from '../../../shared/components/ui/FilterField.jsx'
import { resolveFilters } from './TraceModel.jsx'

// 필터 2줄 + 「조회」 — 시안 v2 03_트레이스뷰어 .filter-bar 스킨 (FilterField 킷)
// draft 는 로컬 state 이고, 적용된 값(=쿼리스트링)이 바뀌면 부모가 key 로 리마운트시켜 동기화한다.
// (useEffect 안에서 setState 하지 않기 위한 패턴)

// 복수 선택 드롭다운 — 표기는 "PH_FOCUS, PH_DOSE (2)" / "1, 3, 5 (3)" 형식
function MultiPick({ label, display, minWidth = 210, children }) {
  const [open, setOpen] = useState(false)
  return (
    <FilterField label={label}>
      <div className="relative">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex h-9 w-full cursor-pointer items-center justify-between gap-5 rounded-lg border border-line bg-white px-3 font-mono text-[13px] font-semibold text-navy"
          style={{ minWidth }}
        >
          {display}
          <span className="text-[11px] text-g2">▾</span>
        </button>
        {open && (
          <>
            <button
              type="button"
              aria-label="선택 닫기"
              onClick={() => setOpen(false)}
              className="fixed inset-0 z-10 cursor-default"
            />
            <div className="absolute left-0 top-[42px] z-20 flex min-w-full flex-col gap-0.5 rounded-lg border border-line bg-white p-1.5">
              {children}
            </div>
          </>
        )}
      </div>
    </FilterField>
  )
}

function PickRow({ on, onToggle, children }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={on}
      className={`flex cursor-pointer items-center gap-2 whitespace-nowrap rounded-md px-2.5 py-1.5 text-left font-mono text-[12.5px] font-semibold ${
        on ? 'bg-tint-blue text-blue' : 'text-g1 hover:bg-soft'
      }`}
    >
      <span className="w-3 flex-none">{on ? '✓' : ''}</span>
      {children}
    </button>
  )
}

const DATE_CLS = 'h-9 rounded-lg border border-line bg-white px-2.5 font-mono text-[12.5px] font-semibold text-navy'

function TraceFilterBar({ rows, value, onSearch }) {
  const [draft, setDraft] = useState(value)
  const f = resolveFilters(rows, draft)
  const o = f.options

  const patch = (next) => setDraft({ ...f, ...next })
  const toggle = (list, v) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])

  return (
    <div className="flex flex-col">
      <div className="flex items-end gap-4 pb-1.5 pt-2">
        <FilterField label="AREA">
          <FilterSelect
            value={f.area}
            options={o.areas}
            onChange={(v) => patch({ area: v, equipment: '', chamber: '', sensors: [], lot: '', wafers: [] })}
          />
        </FilterField>
        <FilterField label="설비">
          <FilterSelect
            value={f.equipment}
            options={o.equipments}
            mono
            onChange={(v) => patch({ equipment: v, chamber: '', sensors: [], lot: '', wafers: [] })}
          />
        </FilterField>
        <FilterField label="챔버">
          <FilterSelect
            value={f.chamber}
            options={o.chambers}
            mono
            onChange={(v) => patch({ chamber: v, sensors: [], lot: '', wafers: [] })}
          />
        </FilterField>
        <MultiPick label="파라미터" display={`${f.sensors.join(', ')} (${f.sensors.length})`}>
          {o.sensors.map((s) => (
            <PickRow key={s} on={f.sensors.includes(s)} onToggle={() => patch({ sensors: toggle(f.sensors, s) })}>
              {s}
            </PickRow>
          ))}
        </MultiPick>
      </div>

      <div className="flex items-end gap-4 pb-4 pt-0">
        {/* TODO(data): recipe_id 실측 미제공 — alarms·trace 어디에도 레시피 식별자가 없다 */}
        <FilterField label="레시피">
          <FilterSelect value="" options={[{ value: '', label: '실측 미제공' }]} disabled onChange={() => {}} />
        </FilterField>
        <FilterField label="LOT">
          <FilterSelect value={f.lot} options={o.lots} mono onChange={(v) => patch({ lot: v, wafers: [] })} />
        </FilterField>
        <MultiPick label="WAFER" display={`${f.wafers.join(', ')} (${f.wafers.length})`} minWidth={150}>
          {/* 전체 = 선택 챔버가 처리한 해당 LOT 의 전 웨이퍼 (기본값) */}
          <PickRow on={f.wafers.length === o.wafers.length} onToggle={() => patch({ wafers: [] })}>
            전체
          </PickRow>
          {o.wafers.map((w) => (
            <PickRow key={w} on={f.wafers.includes(w)} onToggle={() => patch({ wafers: toggle(f.wafers, w) })}>
              W{w}
            </PickRow>
          ))}
        </MultiPick>
        <FilterField label="기간">
          <div className="flex items-center gap-1.5">
            <input
              type="date"
              value={f.from}
              onChange={(e) => patch({ from: e.target.value })}
              aria-label="조회 시작일"
              className={DATE_CLS}
            />
            <span className="text-[13px] font-bold text-g2">~</span>
            <input
              type="date"
              value={f.to}
              onChange={(e) => patch({ to: e.target.value })}
              aria-label="조회 종료일"
              className={DATE_CLS}
            />
          </div>
        </FilterField>
        <Button className="ml-auto" onClick={() => onSearch(f)}>
          조회
        </Button>
      </div>
    </div>
  )
}

export default TraceFilterBar
