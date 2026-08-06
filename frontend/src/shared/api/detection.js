import apiClient, { USE_MOCK, mockResponse } from './client.js'
import { ALARMS } from '../../features/detection/mock/alarms.js'
import { DASHBOARD } from '../../features/detection/mock/dashboard.js'
import { TRACE } from '../../features/detection/mock/trace.js'

export function getDashboard(date, area) {
  if (USE_MOCK) return mockResponse(DASHBOARD)
  return apiClient.get('/dashboard/summary', { params: { date, area } }).then((r) => r.data)
}

// mock에서는 전체 51건 반환 — 필터링은 화면에서 즉시 수행 (dc.html 동작 동일)
export function getAlarms(filter) {
  if (USE_MOCK) return mockResponse(ALARMS)
  return apiClient.get('/alarms', { params: filter }).then((r) => r.data)
}

export function getAlarm(id) {
  if (USE_MOCK) return mockResponse(ALARMS.find((a) => a.alarm_id === id) ?? null)
  return apiClient.get(`/alarms/${id}`).then((r) => r.data)
}

export function getTrace(lotHistId, sensor) {
  if (USE_MOCK) return mockResponse(TRACE)
  return apiClient.get(`/traces/${lotHistId}`, { params: { sensor } }).then((r) => r.data)
}
