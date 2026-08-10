import apiClient, { USE_MOCK, mockResponse } from './client.js'
import { RELATIONS } from '../../features/knowledge/mock/relations.js'
import { DOC_DB, DOC_SCORES } from '../../features/knowledge/mock/documents.js'

export function getChamberRelations(id) {
  if (USE_MOCK) return mockResponse(RELATIONS)
  return apiClient.get(`/relations/chambers/${id}`).then((r) => r.data)
}

// COMMON 문서는 model_code 필터와 무관하게 항상 포함
export function searchDocuments({ query, model_code, top_k = 4 }) {
  if (USE_MOCK) {
    const raw = DOC_DB[query] ?? null
    const results = raw
      ? raw
          .map((d, i) => ({ ...d, score: DOC_SCORES[i] }))
          .filter((d) => !model_code || model_code === '전체' || d.model === 'COMMON' || d.model === model_code)
          .slice(0, top_k)
      : []
    return mockResponse({ query, hits: results, count: results.length })
  }
  return apiClient.post('/documents/search', { query, model_code, top_k }).then((r) => r.data)
}
