// fdc.css .check-row / .check-dot — SQL 검증 체크 항목
function CheckRow({ ok = true, children }) {
  return (
    <span className="flex items-center gap-2 text-[12.5px] text-ink">
      <span
        className={`inline-flex h-4 w-4 flex-none items-center justify-center rounded-full text-[10px] font-extrabold ${
          ok ? 'bg-tint-green text-green' : 'bg-tint-red text-red'
        }`}
      >
        {ok ? '✓' : '✕'}
      </span>
      {children}
    </span>
  )
}

export default CheckRow
