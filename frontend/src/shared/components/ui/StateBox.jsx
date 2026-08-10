// fdc.css .state-box (.state-red / .state-green) — 감사로그 before/after
function StateBox({ tone = 'green', children }) {
  return (
    <span
      className={`inline-flex h-[30px] min-w-[200px] items-center rounded-md border px-3.5 font-mono text-[11.5px] font-semibold ${
        tone === 'red'
          ? 'border-tint-red-line bg-row-red text-red'
          : 'border-tint-green-line bg-state-green-bg text-green'
      }`}
    >
      {children}
    </span>
  )
}

export default StateBox
