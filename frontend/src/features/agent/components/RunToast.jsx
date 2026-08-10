// 승인 결과·409 충돌 사유를 알리는 토스트 (화면 우상단 고정) — fdc 틴트 토큰만 사용
const TONE = {
  ok: { border: 'border-tint-green-line', icon: 'bg-tint-green text-green', title: 'text-green' },
  oos: { border: 'border-tint-red-line', icon: 'bg-tint-red text-red', title: 'text-red' },
  ooc: { border: 'border-tint-amber-line', icon: 'bg-tint-amber text-tint-amber-text', title: 'text-tint-amber-text' },
  info: { border: 'border-tint-blue-line', icon: 'bg-tint-blue text-blue', title: 'text-blue' },
}

function RunToast({ toast, onClose }) {
  if (!toast) return null
  const t = TONE[toast.tone] ?? TONE.info
  return (
    <div className="pointer-events-none fixed right-7 top-[74px] z-50 flex justify-end">
      <div
        className={`pointer-events-auto flex max-w-[420px] animate-[om-fadein_.2s_ease-out] items-start gap-3 rounded-[10px] border bg-white px-4 py-3 shadow-[0_6px_20px_rgba(30,58,92,.14)] ${t.border}`}
      >
        <span
          className={`mt-px flex h-[22px] w-[22px] flex-none items-center justify-center rounded-md text-xs font-extrabold ${t.icon}`}
        >
          !
        </span>
        <div className="flex flex-col gap-1">
          <div className={`font-mono text-[12.5px] font-extrabold ${t.title}`}>{toast.title}</div>
          <div className="text-[13px] leading-[1.5] text-ink">{toast.message}</div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="ml-1 cursor-pointer border-none bg-transparent p-0 text-[15px] font-extrabold text-g2 hover:text-ink"
          aria-label="알림 닫기"
        >
          ×
        </button>
      </div>
    </div>
  )
}

export default RunToast
