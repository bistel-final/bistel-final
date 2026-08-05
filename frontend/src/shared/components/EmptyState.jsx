// 빈 상태 — dc.html 빈 데이터 카드
function EmptyState({
  title = '해당 조건의 데이터가 없습니다',
  description = '필터를 변경해 다시 조회해 주세요.',
}) {
  return (
    <div className="mx-auto my-20 max-w-[560px] rounded-[14px] border border-line bg-white p-9 text-center shadow-[0_2px_8px_rgba(15,42,92,.06)]">
      <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-line-soft text-2xl font-extrabold text-brand">
        0
      </div>
      <div className="text-[19px] font-extrabold text-navy">{title}</div>
      <div className="mt-2 text-[15px] text-slate">{description}</div>
    </div>
  )
}

export default EmptyState
