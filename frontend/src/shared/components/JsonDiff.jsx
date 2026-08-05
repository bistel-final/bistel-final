// 감사로그 before/after JSON diff — 변경 필드 하이라이트
function JsonDiff({ diff }) {
  const line = (value) => `{ "${diff.key}": "${value}" }`
  return (
    <div className="grid grid-cols-2 gap-3">
      <div className="overflow-hidden rounded-lg border border-line bg-white">
        <div className="bg-line-soft px-[13px] py-[7px] font-mono text-[11px] font-extrabold text-slate">
          before_json
        </div>
        <div className="px-[13px] py-2.5 font-mono text-[12.5px] leading-[1.7]">
          <div className="rounded px-1.5 py-px font-bold" style={{ background: '#FEE2E2', color: '#DC2626' }}>
            {line(diff.before)}
          </div>
        </div>
      </div>
      <div className="overflow-hidden rounded-lg border border-line bg-white">
        <div className="bg-line-soft px-[13px] py-[7px] font-mono text-[11px] font-extrabold text-slate">
          after_json
        </div>
        <div className="px-[13px] py-2.5 font-mono text-[12.5px] leading-[1.7]">
          <div className="rounded px-1.5 py-px font-extrabold" style={{ background: '#DCFCE7', color: '#16A34A' }}>
            {line(diff.after)}
          </div>
        </div>
      </div>
    </div>
  )
}

export default JsonDiff
