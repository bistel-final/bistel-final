// 에러 상태 카드
function ErrorState({ title = '데이터를 불러오지 못했습니다', detail, onRetry }) {
  return (
    <div className="mx-auto my-20 max-w-[560px] rounded-[10px] border border-tint-red-line bg-white p-9 text-center">
      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-tint-red text-[26px] font-extrabold text-red">
        !
      </div>
      <div className="text-[17px] font-extrabold text-navy">{title}</div>
      {detail && <div className="mt-2 font-mono text-[13px] text-g1">{detail}</div>}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-5 inline-flex h-[34px] cursor-pointer items-center rounded-lg border border-transparent bg-blue px-[18px] font-sans text-[13px] font-bold text-white hover:bg-navy"
        >
          다시 시도
        </button>
      )}
    </div>
  )
}

export default ErrorState
