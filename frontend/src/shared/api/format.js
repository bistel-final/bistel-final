// CSV 형식 "YYYY-MM-DD HH:mm[:ss]" ↔ API 명세 형식(Asia/Seoul offset ISO 8601) 변환
const SEOUL = '+09:00'

export function toIso(ts) {
  if (!ts) return null
  const value = String(ts)
  // canonical API fixture가 이미 ISO 8601이면 offset을 중복해서 붙이지 않는다.
  if (/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return value
  const withSec = value.length === 16 ? `${value}:00` : value
  return `${withSec.replace(' ', 'T')}${SEOUL}`
}

// 화면 표시용 — ISO를 다시 "MM-DD HH:mm" 등으로 자른다
export function isoToParts(iso) {
  if (!iso) return { date: '', time: '', dateTime: '' }
  const [date, rest] = iso.split('T')
  const time = (rest || '').slice(0, 5)
  return { date, time, dateTime: `${date} ${time}` }
}

export const fmtDateTime = (iso) => isoToParts(iso).dateTime
export const fmtShort = (iso) => {
  const { date, time } = isoToParts(iso)
  return date ? `${date.slice(5)} ${time}` : ''
}
export const fmtTime = (iso) => isoToParts(iso).time

// 목록 응답 규격 {items, total, page, size}. Mock도 실제 API의 기본값과 slicing을 따른다.
export function page(items, { page: requestedPage = 1, size: requestedSize = 20 } = {}) {
  const currentPage = Math.max(1, Number(requestedPage) || 1)
  const size = Math.min(100, Math.max(1, Number(requestedSize) || 20))
  const start = (currentPage - 1) * size
  return {
    items: items.slice(start, start + size),
    total: items.length,
    page: currentPage,
    size,
  }
}
