import { useState } from 'react'
import Button from '../../../shared/components/ui/Button.jsx'
import { FilterField, FilterSelect } from '../../../shared/components/ui/FilterField.jsx'
import { normalizeTraceFilters } from './TraceFilters.js'

const DATE_CLS = 'h-9 rounded-lg border border-line bg-white px-2.5 font-mono text-[12.5px] font-semibold text-navy'

function MultiPick({ label, display, minWidth = 210, children }) {
  const [open, setOpen] = useState(false)
  return (
    <FilterField label={label}>
      <div className="relative">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex h-9 w-full cursor-pointer items-center justify-between gap-5 rounded-lg border border-line bg-white px-3 font-mono text-[13px] font-semibold text-navy"
          style={{ minWidth }}
        >
          {display || '선택 없음'} <span className="text-[11px] text-g2">▾</span>
        </button>
        {open && (
          <>
            <button type="button" aria-label="선택 닫기" onClick={() => setOpen(false)} className="fixed inset-0 z-10" />
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
      className={`flex items-center gap-2 whitespace-nowrap rounded-md px-2.5 py-1.5 text-left font-mono text-[12.5px] font-semibold ${
        on ? 'bg-tint-blue text-blue' : 'text-g1 hover:bg-soft'
      }`}
    >
      <span className="w-3 flex-none">{on ? '✓' : ''}</span>
      {children}
    </button>
  )
}

<<<<<<< Updated upstream
const DATE_CLS = 'h-9 rounded-lg border border-line bg-white px-2.5 font-mono text-[12.5px] font-semibold text-navy'

function TraceFilterBar({ catalog, rows, value, onSearch }) {
  const [draft, setDraft] = useState(value)
  // 선택지: AREA·설비·챔버·파라미터·레시피·LOT 은 catalog, WAFER 는 조회 응답(rows)에서 나온다
  const f = resolveFilters(catalog, rows, draft)
  const o = f.options

  const patch = (next) => setDraft({ ...f, ...next })
  const toggle = (list, v) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
=======
function TraceFilterBar({ catalog, value, onSearch }) {
  const [draft, setDraft] = useState(value)
  const normalized = normalizeTraceFilters(catalog, draft)
  const options = normalized.options
  const patch = (next) => setDraft((current) => ({ ...current, ...next }))
  const toggle = (items, value) => (items.includes(value) ? items.filter((item) => item !== value) : [...items, value])
>>>>>>> Stashed changes

  return (
    <div className="flex flex-col">
      <div className="flex items-end gap-4 pb-1.5 pt-2">
        <FilterField label="AREA">
          <FilterSelect
<<<<<<< Updated upstream
            value={f.area}
            options={o.areas}
            onChange={(v) =>
              patch({ area: v, equipment: '', chamber: '', sensors: [], recipe: '', lot: '', wafers: [] })
            }
=======
            value={normalized.area}
            options={options.areas}
            onChange={(area) => patch({ area, equipment: '', chamber: '' })}
>>>>>>> Stashed changes
          />
        </FilterField>
        <FilterField label="설비">
          <FilterSelect
            value={normalized.equipment}
            options={options.equipments}
            mono
            onChange={(equipment) => patch({ equipment, chamber: '' })}
          />
        </FilterField>
        <FilterField label="챔버">
          <FilterSelect value={normalized.chamber} options={options.chambers} mono onChange={(chamber) => patch({ chamber })} />
        </FilterField>
        <MultiPick label="파라미터" display={`${normalized.sensors.join(', ')} (${normalized.sensors.length})`}>
          {options.sensors.map((sensor) => (
            <PickRow
              key={sensor}
              on={normalized.sensors.includes(sensor)}
              onToggle={() => patch({ sensors: toggle(normalized.sensors, sensor) })}
            >
              {sensor}
            </PickRow>
          ))}
        </MultiPick>
      </div>

      <div className="flex items-end gap-4 pb-4 pt-0">
        <FilterField label="레시피">
<<<<<<< Updated upstream
          <FilterSelect value={f.recipe} options={o.recipes} mono onChange={(v) => patch({ recipe: v })} />
=======
          <FilterSelect
            value={normalized.recipe}
            options={options.recipes.length ? [{ value: '', label: '전체' }, ...options.recipes] : [{ value: '', label: '전체' }]}
            disabled={!options.recipes.length}
            onChange={(recipe) => patch({ recipe })}
          />
>>>>>>> Stashed changes
        </FilterField>
        <FilterField label="LOT">
          <FilterSelect value={normalized.lot} options={options.lots} mono onChange={(lot) => patch({ lot, wafers: [] })} />
        </FilterField>
        <MultiPick label="WAFER" display={`${normalized.wafers.join(', ')} (${normalized.wafers.length})`} minWidth={150}>
          <PickRow on={normalized.wafers.length === options.wafers.length} onToggle={() => patch({ wafers: [] })}>
            전체
          </PickRow>
          {options.wafers.map((wafer) => (
            <PickRow
              key={wafer}
              on={normalized.wafers.includes(wafer)}
              onToggle={() => patch({ wafers: toggle(normalized.wafers, wafer) })}
            >
              W{wafer}
            </PickRow>
          ))}
        </MultiPick>
        <FilterField label="기간">
          <div className="flex items-center gap-1.5">
            <input type="date" value={normalized.from} onChange={(event) => patch({ from: event.target.value })} className={DATE_CLS} />
            <span className="text-[13px] font-bold text-g2">~</span>
            <input type="date" value={normalized.to} onChange={(event) => patch({ to: event.target.value })} className={DATE_CLS} />
          </div>
        </FilterField>
        <Button className="ml-auto" onClick={() => onSearch(normalized)}>
          조회
        </Button>
      </div>
    </div>
  )
}

export default TraceFilterBar
