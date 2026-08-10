// fdc.css .card / .card-h / .card-t / .card-note
export function Card({ className = '', style, children }) {
  return (
    <div className={`rounded-[10px] border border-line bg-white ${className}`} style={style}>
      {children}
    </div>
  )
}

export function CardHeader({ title, note, mono = false, className = '' }) {
  return (
    <div className={`flex items-baseline justify-between px-5 pb-3 pt-4 ${className}`}>
      <span className={`text-[15px] font-extrabold text-navy ${mono ? 'font-mono' : ''}`}>{title}</span>
      {note && <span className="text-[11.5px] text-g1">{note}</span>}
    </div>
  )
}

// 점선 안내 카드 (.dashed-card)
export function DashedCard({ className = '', children }) {
  return <div className={`rounded-[10px] border-[1.5px] border-dashed border-dash-line bg-white ${className}`}>{children}</div>
}
