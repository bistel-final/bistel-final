// fdc.css .kv — mean/std/min/max 통계 그리드
function KVGrid({ items }) {
  return (
    <div className="grid grid-cols-2 gap-x-[18px] gap-y-2.5">
      {items.map(([k, v]) => (
        <div key={k}>
          <div className="font-mono text-[10.5px] text-g1">{k}</div>
          <div className="mt-0.5 font-mono text-sm font-bold text-navy">{v}</div>
        </div>
      ))}
    </div>
  )
}

export default KVGrid
