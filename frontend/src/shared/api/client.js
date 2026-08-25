import axios from 'axios'

// Vite 는 import.meta.env 를 정적 치환한다. node 로 직접 import 하는 스키마 테스트에서는
// undefined 이므로 빈 객체로 폴백한다.
const env = import.meta.env ?? {}

// VITE_USE_MOCK 기본 true — 'false'로 명시해야 실제 API 호출
export const USE_MOCK = env.VITE_USE_MOCK !== 'false'

// 도메인별 오버라이드 — 백엔드에 아직 없는 영역(A/C)만 mock으로 남기고
// 구현된 영역(D analytics · B documents)은 실서버로 보내기 위해 쓴다.
// 예) VITE_USE_MOCK=false + VITE_USE_MOCK_DETECTION=true — 미설정 시 전역 값을 따른다.
// TODO(api): detection·agent 라우터 구현 완료 시 이 오버라이드를 제거한다.
export const useMockFor = (domain) => {
  const value = env[`VITE_USE_MOCK_${domain}`]
  return value == null ? USE_MOCK : value !== 'false'
}

export const MOCK_DELAY_MS = 300
export const ANALYTICS_QUERY_TIMEOUT_MS = 150000

// mock 데이터를 실제 API처럼 지연 반환
export function mockResponse(data) {
  return new Promise((resolve) => {
    setTimeout(() => resolve(structuredClone(data)), MOCK_DELAY_MS)
  })
}

const apiClient = axios.create({
  baseURL: env.VITE_API_BASE_URL || '/api',
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
})

export default apiClient
