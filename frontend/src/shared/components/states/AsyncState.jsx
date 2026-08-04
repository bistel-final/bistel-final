export function LoadingState({ message = '데이터를 불러오는 중입니다.' }) {
  return <div className="state-card state-loading">{message}</div>
}

export function ErrorState({ message = '데이터를 불러오지 못했습니다.' }) {
  return <div className="state-card state-error">{message}</div>
}

export function EmptyState({ message = '표시할 데이터가 없습니다.' }) {
  return <div className="state-card state-empty">{message}</div>
}
