// 로딩 상태 — 스피너 + 메시지 (+선택적 스켈레톤)
function LoadingState({ message = '데이터를 불러오는 중…', children }) {
  return (
    <div className="flex flex-col gap-4 pt-6">
      <div className="flex items-center gap-3 py-2 text-sm font-semibold text-g1">
        <span className="h-5 w-5 animate-[om-spin_.8s_linear_infinite] rounded-full border-[3px] border-tint-blue border-t-blue" />
        {message}
      </div>
      {children}
    </div>
  )
}

export function Skeleton({ className = '' }) {
  return (
    <div
      className={`animate-[om-shimmer_1.4s_linear_infinite] rounded-[10px] bg-[linear-gradient(90deg,#EFF2F5_25%,#F8FAFC_50%,#EFF2F5_75%)] bg-[length:800px_100%] ${className}`}
    />
  )
}

export default LoadingState
