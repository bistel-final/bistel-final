// 빈 상태 카드
function EmptyState({ title = '해당 조건의 데이터가 없습니다', description = '필터를 조정해 다시 조회해 주세요.' }) {
  return (
    <div className="mx-auto my-20 max-w-[560px] rounded-[10px] border-[1.5px] border-dashed border-dash-line bg-white p-9 text-center">
      <div className="text-[16px] font-extrabold text-navy">{title}</div>
      <div className="mt-2 text-[13px] text-g1">{description}</div>
    </div>
  )
}

export default EmptyState
