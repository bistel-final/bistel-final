// fdc.css .f-field / .f-label / .select — 실제 select를 시안 스킨으로
export function FilterField({ label, children }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-[11px] text-g1">{label}</span>
      {children}
    </div>
  )
}

export function FilterSelect({ value, onChange, options, minWidth = 150, mono = false, disabled = false }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
      disabled={disabled}
      className={`h-9 rounded-lg border border-line bg-white px-3 text-[13px] font-semibold text-navy disabled:text-g2 ${mono ? 'font-mono' : ''}`}
      style={{ minWidth }}
    >
      {options.map((o) => {
        const opt = typeof o === 'string' ? { value: o, label: o } : o
        return (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        )
      })}
    </select>
  )
}

// 정적 표시용 (기간 등 아직 동작하지 않는 필터)
export function FilterStatic({ children, minWidth = 150, mono = false }) {
  return (
    <span
      className={`flex h-9 items-center justify-between gap-5 rounded-lg border border-line bg-white px-3 text-[13px] font-semibold text-navy ${mono ? 'font-mono' : ''}`}
      style={{ minWidth }}
    >
      {children}
      <span className="text-[11px] text-g2">▾</span>
    </span>
  )
}

export function FilterBar({ className = '', children }) {
  return <div className={`flex items-end gap-4 pb-4 pt-2 ${className}`}>{children}</div>
}

export function FilterNote({ children }) {
  return <div className="ml-auto pb-2.5 text-xs text-g1">{children}</div>
}
