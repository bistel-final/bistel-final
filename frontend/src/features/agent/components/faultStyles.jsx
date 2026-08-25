import { FAULT_BADGE_CLS } from './agentModel.js'

// Fault 뱃지 — 색 매핑은 agentModel.js (fast refresh 제약으로 이 파일은 컴포넌트만 export)
export function FaultBadge({ code, name, className = '' }) {
  const cls = FAULT_BADGE_CLS[code] ?? FAULT_BADGE_CLS.OTH
  return (
    <span
      className={`inline-flex h-5 items-center gap-1 whitespace-nowrap rounded-full border px-2.5 font-mono text-[10.5px] font-bold ${cls} ${className}`}
    >
      {code}
      {name && <span className="font-sans font-semibold">{name}</span>}
    </span>
  )
}

export default FaultBadge
