// 에러 상태 — dc.html 에러 카드
function ErrorState({
  title = '데이터를 불러오지 못했습니다',
  detail,
  onRetry,
}) {
  return (
    <div className="mx-auto my-20 max-w-[560px] rounded-[14px] border border-[#FECACA] bg-white p-9 text-center shadow-[0_2px_8px_rgba(15,42,92,.06)]">
      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-oos-soft text-[26px] font-extrabold text-oos">
        !
      </div>
      <div className="text-[19px] font-extrabold text-navy">{title}</div>
      {detail && <div className="mt-2 font-mono text-[15px] text-slate">{detail}</div>}
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-5 cursor-pointer rounded-lg border-none bg-brand px-[26px] py-[11px] font-sans text-[15px] font-bold text-white hover:bg-brand-light"
        >
          다시 시도
        </button>
      )}
    </div>
  )
}

export default ErrorState
