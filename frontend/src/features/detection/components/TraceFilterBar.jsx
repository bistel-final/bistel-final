import { useState } from 'react'
import { resolveFilters } from './TraceModel.jsx'

// 필터 2줄 + 조회 버튼.
// draft 는 로컬 state 이고, 적용된 값(=쿼리스트링)이 바뀌면 부모가 key 로 리마운트시켜 동기화한다.
// (useEffect 안에서 setState 하지 않기 위한 패턴)

const LABEL = 'text-[12.5px] font-bold text-slate'
const SELECT =
  'rounded-lg border border-line-input bg-white px-2.5 py-[7px] font-mono text-[13px] font-bold text-navy disabled:bg-page disabled:text-slate-light'
const DATE = 'rounded-lg border border-line-input bg-white px-2.5 py-[7px] font-mono text-[13px] font-semibold text-navy'

function Chip({ on, children, onClick, tone = 'brand' }) {
  const bg = tone === 'brand' ? '#1E5FC2' : '#0F2A5C'
  return (
    <button
      type="button"
      onClick={onClick}
      className="cursor-pointer rounded-full border px-[11px] py-[5px] font-mono text-[11.5px] font-extrabold"
      style={
        on
          ? { background: bg, color: '#FFFFFF', borderColor: bg }
          : { background: '#FFFFFF', color: '#475569', borderColor: '#CBD5E1' }
      }
    >
      {children}
    </button>
  )
}

function Field({ label, value, options, onChange, disabled, placeholder }) {
  return (
    <label className="flex items-center gap-2">
      <span className={LABEL}>{label}</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className={SELECT}
      >
        {disabled && <option value="">{placeholder}</option>}
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  )
}

function TraceFilterBar({ rows, value, onSearch }) {
  const [draft, setDraft] = useState(value)
  const f = resolveFilters(rows, draft)
  const o = f.options

  const patch = (next) => setDraft({ ...f, ...next })
  const toggle = (list, v) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])

  return (
    <div className="flex flex-col gap-2.5 rounded-xl border border-line bg-white px-[18px] py-3.5 shadow-[0_1px_3px_rgba(15,42,92,.05)]">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2.5">
        <Field
          label="AREA"
          value={f.area}
          options={o.areas}
          onChange={(v) => patch({ area: v, equipment: '', chamber: '', sensors: [], lot: '', wafers: [] })}
        />
        <Field
          label="설비"
          value={f.equipment}
          options={o.equipments}
          onChange={(v) => patch({ equipment: v, chamber: '', sensors: [], lot: '', wafers: [] })}
        />
        <Field
          label="챔버"
          value={f.chamber}
          options={o.chambers}
          onChange={(v) => patch({ chamber: v, sensors: [], lot: '', wafers: [] })}
        />
        <div className="flex items-center gap-2">
          <span className={LABEL}>파라미터</span>
          <div className="flex flex-wrap gap-[6px]">
            {o.sensors.map((s) => (
              <Chip key={s} on={f.sensors.includes(s)} onClick={() => patch({ sensors: toggle(f.sensors, s) })}>
                {s}
              </Chip>
            ))}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2.5">
        {/* TODO(data): recipe_id 실측 미제공 — alarms·trace 어디에도 레시피 식별자가 없다 */}
        <Field label="레시피" value="" options={[]} onChange={() => {}} disabled placeholder="실측 미제공" />
        <Field label="LOT" value={f.lot} options={o.lots} onChange={(v) => patch({ lot: v, wafers: [] })} />
        <div className="flex items-center gap-2">
          <span className={LABEL}>WAFER</span>
          <div className="flex flex-wrap items-center gap-[6px]">
            <Chip tone="navy" on={f.wafers.length === o.wafers.length} onClick={() => patch({ wafers: [] })}>
              전체
            </Chip>
            {o.wafers.map((w) => (
              <Chip key={w} on={f.wafers.includes(w)} onClick={() => patch({ wafers: toggle(f.wafers, w) })}>
                W{w}
              </Chip>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={LABEL}>기간</span>
          <input type="date" value={f.from} onChange={(e) => patch({ from: e.target.value })} className={DATE} />
          <span className="text-[13px] font-bold text-[#94A3B8]">~</span>
          <input type="date" value={f.to} onChange={(e) => patch({ to: e.target.value })} className={DATE} />
        </div>
        <button
          type="button"
          onClick={() => onSearch(f)}
          className="ml-auto cursor-pointer rounded-lg border-none bg-brand px-[22px] py-[9px] font-sans text-[13.5px] font-extrabold text-white hover:bg-brand-light"
        >
          조회
        </button>
      </div>

      <div className="text-[11.5px] font-semibold text-slate-light">
        WAFER 기본값은 <span className="font-mono font-extrabold text-slate">선택 챔버가 처리한 해당 LOT 의 전 웨이퍼</span>다.
        필터는 쿼리스트링에 직렬화되어 새로고침·링크 공유 시 동일 화면이 복원된다.
      </div>
    </div>
  )
}

export default TraceFilterBar
