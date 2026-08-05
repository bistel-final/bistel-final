import axios from 'axios'

// VITE_USE_MOCK 기본 true — 'false'로 명시해야 실제 API 호출
export const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false'

export const MOCK_DELAY_MS = 300

// mock 데이터를 실제 API처럼 지연 반환
export function mockResponse(data) {
  return new Promise((resolve) => {
    setTimeout(() => resolve(structuredClone(data)), MOCK_DELAY_MS)
  })
}

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
})

export default apiClient
