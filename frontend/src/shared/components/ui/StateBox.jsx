// fdc.css .state-box (.state-red / .state-green) — 감사로그 before/after
// before/after 는 dict 다 ({status:'PENDING'}) — 객체를 받으면 "키 = 값"으로 편다.
// 키가 여럿이면 콤마로 잇는다. (문자열을 그대로 넘기던 시절의 [object Object] 방지)
const fmtState = (v) => {
  if (v == null) return null
  if (typeof v !== 'object' || Array.isArray(v)) return v
  return Object.entries(v)
    .map(([k, val]) => `${k} = ${val}`)
    .join(', ')
}

function StateBox({ tone = 'green', children }) {
  return (
    <span
      className={`inline-flex h-[30px] min-w-[200px] items-center rounded-md border px-3.5 font-mono text-[11.5px] font-semibold ${
        tone === 'red'
          ? 'border-tint-red-line bg-row-red text-red'
          : 'border-tint-green-line bg-state-green-bg text-green'
      }`}
    >
      {fmtState(children)}
    </span>
  )
}

export default StateBox
