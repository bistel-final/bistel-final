// 대시보드 KPI 카드 — hover 시 살짝 떠오르는 흰 카드
function KpiCard({ title, value, unit, valueColor = '#0F2A5C', children }) {
  return (
    <div className="rounded-xl border border-line bg-white px-5 py-[18px] shadow-[0_1px_3px_rgba(15,42,92,.05)] transition-[transform,box-shadow] duration-[180ms] hover:-translate-y-[3px] hover:shadow-[0_8px_20px_rgba(15,42,92,.12)]">
      <div className="text-[13.5px] font-bold text-slate">{title}</div>
      <div className="mt-1.5 font-mono text-4xl font-extrabold" style={{ color: valueColor }}>
        {value}
        {unit && <span className="ml-[3px] text-[17px] font-bold text-slate">{unit}</span>}
      </div>
      {children && <div className="mt-1.5 text-[13px] font-semibold">{children}</div>}
    </div>
  )
}

export default KpiCard
