// CSV 형식 "YYYY-MM-DD HH:mm[:ss]" ↔ API 명세 형식(Asia/Seoul offset ISO 8601) 변환
const SEOUL = '+09:00'

export function toIso(ts) {
  if (!ts) return null
  const withSec = ts.length === 16 ? `${ts}:00` : ts
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

// 목록 응답 규격 {items, total, page, size}
export function page(items, { page: p = 1, size = items.length } = {}) {
  return { items, total: items.length, page: p, size }
}
