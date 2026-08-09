// 승인 결과·409 충돌 사유를 알리는 토스트 (화면 우상단 고정)
const TONE = {
  ok: { bg: '#DCFCE7', border: '#86EFAC', color: '#16A34A' },
  oos: { bg: '#FEE2E2', border: '#FECACA', color: '#DC2626' },
  ooc: { bg: '#FEF3C7', border: '#FDE68A', color: '#D97706' },
  info: { bg: '#EDF2FA', border: '#C7DBF7', color: '#1E5FC2' },
}

function RunToast({ toast, onClose }) {
  if (!toast) return null
  const t = TONE[toast.tone] ?? TONE.info
  return (
    <div className="pointer-events-none fixed right-7 top-[74px] z-50 flex justify-end">
      <div
        className="pointer-events-auto flex max-w-[420px] animate-[om-fadein_.2s_ease-out] items-start gap-3 rounded-xl border bg-white px-4 py-3 shadow-[0_6px_20px_rgba(15,42,92,.14)]"
        style={{ borderColor: t.border }}
      >
        <span
          className="mt-px flex h-[22px] w-[22px] flex-none items-center justify-center rounded-md text-[12px] font-extrabold"
          style={{ background: t.bg, color: t.color }}
        >
          !
        </span>
        <div className="flex flex-col gap-1">
          <div className="font-mono text-[12.5px] font-extrabold" style={{ color: t.color }}>
            {toast.title}
          </div>
          <div className="text-[13px] font-semibold leading-[1.5] text-ink">{toast.message}</div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="ml-1 cursor-pointer border-none bg-transparent p-0 text-[15px] font-extrabold text-slate-light hover:text-ink"
          aria-label="알림 닫기"
        >
          ×
        </button>
      </div>
    </div>
  )
}

export default RunToast
