import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getDocument, searchDocuments } from '../../../shared/api/knowledge.js'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card, CardHeader, DashedCard } from '../../../shared/components/ui/Card.jsx'
import DocumentDetailDrawer from '../components/DocumentDetailDrawer.jsx'
import DocumentSearchResultCard from '../components/DocumentSearchResultCard.jsx'
import { DOC_CHIPS, DOC_FILTERS } from '../mock/documents.js'

const ALL_MODELS = '전체'
const ALL_DOC_TYPES = '전체'
const DOC_TYPE_FILTERS = ['전체', 'SPEC', 'TROUBLESHOOT']
const TOP_K_OPTIONS = [4, 6, 10]
const DOCUMENT_LIBRARY = [
  {
    group: 'Troubleshooting',
    items: [
      {
        document_id: 'DOC-TROUBLE-FDC',
        title: 'FDC Fault Guide',
        meta: 'COMMON · 조치 기준',
        model_code: 'COMMON',
        doc_type: 'TROUBLESHOOT',
      },
    ],
  },
  {
    group: 'Equipment SPEC',
    items: [
      {
        document_id: 'DOC-SPEC-PH9000',
        title: 'PH-9000 Photo Scanner',
        meta: 'PH-9000 · Photo',
        model_code: 'PH-9000',
        doc_type: 'SPEC',
      },
      {
        document_id: 'DOC-SPEC-ET7500',
        title: 'ET-7500 Dry Etcher',
        meta: 'ET-7500 · Etch',
        model_code: 'ET-7500',
        doc_type: 'SPEC',
      },
    ],
  },
]

// 문서 검색 — 라이트 시안 4번
// 좌 280px: 추천 질의 + 문서 라이브러리 / 우측: 결과 카드 / 하단: 입력 + [검색]
function DocumentsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const documentRequestRef = useRef(0)
  const urlQuery = searchParams.get('query') ?? ''
  const urlModelCode = searchParams.get('model_code')
  const urlDocType = searchParams.get('doc_type')
  const urlTopK = Number(searchParams.get('top_k'))
  const urlDocumentId = searchParams.get('document_id')
  const urlChunkId = searchParams.get('chunk_id')
  const [input, setInput] = useState(urlQuery)
  const [result, setResult] = useState(null) // { query, hits }
  const [modelCode, setModelCode] = useState(DOC_FILTERS.includes(urlModelCode) ? urlModelCode : ALL_MODELS)
  const [docType, setDocType] = useState(DOC_TYPE_FILTERS.includes(urlDocType) ? urlDocType : ALL_DOC_TYPES)
  const [topK, setTopK] = useState(TOP_K_OPTIONS.includes(urlTopK) ? urlTopK : TOP_K_OPTIONS[0])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [selectedHit, setSelectedHit] = useState(null)
  const [documentDetail, setDocumentDetail] = useState(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState(null)
  const urlSearchLoadedRef = useRef(false)
  const urlDocumentHandledRef = useRef('')

  const syncUrl = useCallback(
    ({
      query,
      model_code,
      doc_type,
      top_k,
      document_id,
      chunk_id,
      clearDocument = false,
    }) => {
      const next = new URLSearchParams(searchParams)
      if (query !== undefined) {
        const normalizedQuery = query.trim()
        if (normalizedQuery) next.set('query', normalizedQuery)
        else next.delete('query')
      }
      if (model_code !== undefined) {
        if (model_code && model_code !== ALL_MODELS) next.set('model_code', model_code)
        else next.delete('model_code')
      }
      if (doc_type !== undefined) {
        if (doc_type && doc_type !== ALL_DOC_TYPES) next.set('doc_type', doc_type)
        else next.delete('doc_type')
      }
      if (top_k !== undefined) {
        if (top_k && top_k !== TOP_K_OPTIONS[0]) next.set('top_k', String(top_k))
        else next.delete('top_k')
      }
      if (clearDocument) {
        next.delete('document_id')
        next.delete('chunk_id')
      }
      if (document_id !== undefined) {
        if (document_id) next.set('document_id', document_id)
        else next.delete('document_id')
      }
      if (chunk_id !== undefined) {
        if (chunk_id) next.set('chunk_id', chunk_id)
        else next.delete('chunk_id')
      }
      setSearchParams(next, { replace: false })
    },
    [searchParams, setSearchParams],
  )

  useEffect(() => {
    if (urlDocumentId) return
    Promise.resolve().then(() => setDetailOpen(false))
  }, [urlDocumentId])

  useEffect(() => {
    if (!urlDocumentId) return
    const urlDocumentKey = `${urlDocumentId}:${urlChunkId ?? ''}`
    if (urlDocumentHandledRef.current === urlDocumentKey) {
      return
    }
    urlDocumentHandledRef.current = urlDocumentKey
    const requestToken = ++documentRequestRef.current
    Promise.resolve().then(() => {
      if (documentRequestRef.current !== requestToken) return
      setSelectedHit({ document_id: urlDocumentId, chunk_id: urlChunkId, title: urlDocumentId })
      setDetailOpen(true)
      setDetailLoading(true)
      setDetailError(null)
      return getDocument(urlDocumentId)
        .then(
          (detail) => {
            if (documentRequestRef.current !== requestToken) return
            setDocumentDetail(detail)
            if (!detail) setDetailError('문서를 찾을 수 없습니다.')
          },
          () => {
            if (documentRequestRef.current === requestToken) {
              setDocumentDetail(null)
              setDetailError('문서 상세를 불러오지 못했습니다.')
            }
          },
        )
        .finally(() => {
          if (documentRequestRef.current === requestToken) setDetailLoading(false)
        })
    })
    return () => {
      if (documentRequestRef.current === requestToken) documentRequestRef.current += 1
    }
  }, [urlDocumentId, urlChunkId])

  const run = useCallback((query, options = {}) => {
    const q = query.trim()
    if (!q || loading) return
    const nextModelCode = options.modelCodeOverride ?? modelCode
    const nextDocType = options.docTypeOverride ?? docType
    const nextTopK = options.topKOverride ?? topK
    setLoading(true)
    documentRequestRef.current += 1
    setDetailOpen(false)
    setSelectedHit(null)
    setDocumentDetail(null)
    setDetailLoading(false)
    setDetailError(null)
    if (options.syncUrl !== false) {
      syncUrl({
        query: q,
        model_code: nextModelCode,
        doc_type: nextDocType,
        top_k: nextTopK,
        clearDocument: true,
      })
    }
    searchDocuments({
      query: q,
      model_code: nextModelCode === ALL_MODELS ? undefined : nextModelCode,
      doc_type: nextDocType === ALL_DOC_TYPES ? undefined : nextDocType,
      top_k: nextTopK,
    })
      .then((res) => {
        setResult({ ...res, model_code: nextModelCode, doc_type: nextDocType, top_k: nextTopK })
        setInput('')
        setError(null)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [
    docType,
    loading,
    modelCode,
    setDetailError,
    setDetailLoading,
    setDetailOpen,
    setDocumentDetail,
    setError,
    setInput,
    setLoading,
    setResult,
    setSelectedHit,
    syncUrl,
    topK,
  ])

  const runRecommendedSearch = (item) => {
    const nextModelCode = modelCode === ALL_MODELS ? item.model_codes[0] : modelCode
    const nextDocType = docType === ALL_DOC_TYPES ? item.doc_types[0] : docType
    setModelCode(nextModelCode)
    setDocType(nextDocType)
    run(item.query, {
      modelCodeOverride: nextModelCode,
      docTypeOverride: nextDocType,
    })
  }

  useEffect(() => {
    const q = urlQuery.trim()
    if (urlSearchLoadedRef.current || !q || urlDocumentId) return
    urlSearchLoadedRef.current = true
    run(q, {
      modelCodeOverride: DOC_FILTERS.includes(urlModelCode) ? urlModelCode : ALL_MODELS,
      docTypeOverride: DOC_TYPE_FILTERS.includes(urlDocType) ? urlDocType : ALL_DOC_TYPES,
      topKOverride: TOP_K_OPTIONS.includes(urlTopK) ? urlTopK : TOP_K_OPTIONS[0],
      syncUrl: false,
    })
  }, [run, urlDocType, urlDocumentId, urlModelCode, urlQuery, urlTopK])

  const openDocument = (hit) => {
    const requestToken = ++documentRequestRef.current
    setSelectedHit(hit)
    setDetailOpen(true)
    setDetailLoading(true)
    setDetailError(null)
    urlDocumentHandledRef.current = `${hit.document_id}:${hit.chunk_id ?? ''}`
    syncUrl({
      document_id: hit.document_id,
      chunk_id: hit.chunk_id,
    })
    getDocument(hit.document_id)
      .then((detail) => {
        if (documentRequestRef.current !== requestToken) return
        setDocumentDetail(detail)
        if (!detail) setDetailError('문서를 찾을 수 없습니다.')
      })
      .catch((e) => {
        if (documentRequestRef.current !== requestToken) return
        setDocumentDetail(null)
        setDetailError(e.message)
      })
      .finally(() => {
        if (documentRequestRef.current !== requestToken) return
        setDetailLoading(false)
      })
  }

  const openLibraryDocument = (document) => {
    const requestToken = ++documentRequestRef.current
    setSelectedHit({
      document_id: document.document_id,
      chunk_id: null,
      title: document.title,
    })
    setDetailOpen(true)
    setDetailLoading(true)
    setDetailError(null)
    urlDocumentHandledRef.current = `${document.document_id}:`
    syncUrl({
      document_id: document.document_id,
      chunk_id: null,
    })
    getDocument(document.document_id)
      .then((detail) => {
        if (documentRequestRef.current !== requestToken) return
        setDocumentDetail(detail)
        if (!detail) setDetailError('문서를 찾을 수 없습니다.')
      })
      .catch((e) => {
        if (documentRequestRef.current !== requestToken) return
        setDocumentDetail(null)
        setDetailError(e.message)
      })
      .finally(() => {
        if (documentRequestRef.current !== requestToken) return
        setDetailLoading(false)
      })
  }

  const closeDocument = () => {
    setDetailOpen(false)
    setSelectedHit(null)
    setDocumentDetail(null)
    setDetailError(null)
    urlDocumentHandledRef.current = ''
    syncUrl({
      document_id: null,
      chunk_id: null,
    })
  }

  const navigateDetailChunk = (chunk) => {
    if (!selectedHit || !documentDetail) return
    const nextHit = {
      ...selectedHit,
      document_id: documentDetail.document_id,
      chunk_id: chunk.chunk_id,
      section: chunk.section_title ?? selectedHit.section,
    }
    setSelectedHit(nextHit)
    urlDocumentHandledRef.current = `${documentDetail.document_id}:${chunk.chunk_id}`
    syncUrl({
      document_id: documentDetail.document_id,
      chunk_id: chunk.chunk_id,
    })
  }

  const filteredHits = result?.hits ?? []

  const filteredLibrary = useMemo(
    () =>
      DOCUMENT_LIBRARY.map((group) => ({
        ...group,
        items: group.items.filter((document) => {
          const matchesModel =
            modelCode === ALL_MODELS || document.model_code === 'COMMON' || document.model_code === modelCode
          const matchesDocType = docType === ALL_DOC_TYPES || document.doc_type === docType
          return matchesModel && matchesDocType
        }),
      })).filter((group) => group.items.length),
    [docType, modelCode],
  )

  const filteredRecommendedGroups = useMemo(
    () =>
      DOC_CHIPS.map((group) => ({
        ...group,
        items: group.items.filter((item) => {
          const matchesModel = modelCode === ALL_MODELS || item.model_codes.includes(modelCode)
          const matchesDocType = docType === ALL_DOC_TYPES || item.doc_types.includes(docType)
          return matchesModel && matchesDocType
        }),
      })).filter((group) => group.items.length),
    [docType, modelCode],
  )

  if (error)
    return (
      <ErrorState
        detail={error}
        onRetry={() => {
          setError(null)
        }}
      />
    )

  return (
    <div className="relative flex h-[calc(100vh-72px)] overflow-hidden animate-[om-fadein_.3s_ease-out] flex-col">
      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[20px] font-extrabold text-ink">문서 검색</div>
        <div className="text-[11.5px] text-g2">설비 SPEC · Fault 가이드 RAG 검색</div>
      </div>

      <div className="flex min-h-0 flex-1 items-stretch gap-4">
        <Card className="flex w-[280px] flex-none flex-col overflow-y-auto">
          <CardHeader title="문서 탐색" />
          <div className="px-4 pb-3">
            <div className="mb-2 text-[11px] font-bold text-g2">검색 필터</div>
            <div className="flex flex-col gap-2.5">
              <label className="flex flex-col gap-1">
                <span className="text-[10.5px] font-bold text-faint">설비 모델</span>
                <select
                  value={modelCode}
                  onChange={(e) => setModelCode(e.target.value)}
                  className="h-9 rounded-lg border border-field-line bg-white px-3 font-mono text-[12px] font-bold text-ink"
                >
                  {DOC_FILTERS.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-[10.5px] font-bold text-faint">문서 유형</span>
                <select
                  value={docType}
                  onChange={(e) => setDocType(e.target.value)}
                  className="h-9 rounded-lg border border-field-line bg-white px-3 font-mono text-[12px] font-bold text-ink"
                >
                  {DOC_TYPE_FILTERS.map((item) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-[10.5px] font-bold text-faint">결과 수</span>
                <select
                  value={topK}
                  onChange={(e) => setTopK(Number(e.target.value))}
                  className="h-9 rounded-lg border border-field-line bg-white px-3 font-mono text-[12px] font-bold text-ink"
                >
                  {TOP_K_OPTIONS.map((item) => (
                    <option key={item} value={item}>
                      {item}개
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>

          <div className="border-t border-cell-line px-4 pb-3 pt-3">
            <div className="mb-2 text-[11px] font-bold text-g2">추천 질문</div>
            <div className="flex flex-col divide-y divide-cell-line border-y border-cell-line">
              {filteredRecommendedGroups.flatMap((group) => group.items).map((item) => (
                <button
                  key={item.label}
                  type="button"
                  onClick={() => runRecommendedSearch(item)}
                  className="cursor-pointer px-0.5 py-2.5 text-left text-[12px] font-semibold leading-5 text-ink transition hover:bg-tint-blue hover:text-blue-hover"
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-3 border-t border-cell-line px-4 pb-3 pt-3">
            <div className="mb-2 text-[11px] font-bold text-g2">문서 라이브러리</div>
            <div className="flex flex-col gap-3">
              {filteredLibrary.map((group) => (
                <div key={group.group}>
                  <div className="mb-1.5 font-mono text-[10px] font-bold uppercase text-faint">{group.group}</div>
                  <div className="flex flex-col gap-1.5">
                    {group.items.map((document) => (
                      <button
                        key={document.document_id}
                        type="button"
                        onClick={() => openLibraryDocument(document)}
                        className={`cursor-pointer rounded-lg border px-3 py-2 text-left transition ${
                          selectedHit?.document_id === document.document_id && detailOpen
                            ? 'border-blue bg-row-sel'
                            : 'border-cell-line bg-white hover:border-blue hover:bg-tint-blue'
                        }`}
                      >
                        <div className="truncate text-[12px] font-extrabold text-ink" title={document.title}>
                          {document.title}
                        </div>
                        <div className="mt-0.5 font-mono text-[10.5px] font-bold text-g2">{document.meta}</div>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

        </Card>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto">
            {!result ? (
              <DashedCard className="flex h-full flex-col items-center justify-center gap-2 p-10 text-center">
                <div className="text-[14px] font-extrabold text-ink">관련 문서 근거를 찾아보세요</div>
                <div className="text-[12px] text-g1">
                  설비 증상, 센서명, 조치 기준을 입력하면 관련 청크를 검색합니다.
                </div>
              </DashedCard>
            ) : result.hits.length === 0 ? (
              <DashedCard className="flex h-full flex-col items-center justify-center gap-2 p-10 text-center">
                <div className="text-[14px] font-extrabold text-ink">검색 결과가 없습니다</div>
                <div className="text-[12px] text-g1">다른 표현으로 다시 검색해 주세요.</div>
              </DashedCard>
            ) : filteredHits.length === 0 ? (
              <DashedCard className="flex h-full flex-col items-center justify-center gap-2 p-10 text-center">
                <div className="text-[14px] font-extrabold text-ink">필터와 일치하는 결과가 없습니다</div>
                <div className="text-[12px] text-g1">문서 유형을 전체로 바꾸거나 검색어를 다시 입력해 주세요.</div>
              </DashedCard>
            ) : (
              <div className="flex flex-col gap-3">
                <div className="text-[12px] text-g1">
                  <span className="font-semibold text-ink">“{result.query}”</span> 검색 결과{' '}
                  <span className="font-mono">{filteredHits.length}</span>건
                  <span className="ml-2 font-mono text-[11px] text-faint">model {result.model_code}</span>
                  {docType !== ALL_DOC_TYPES && <span className="ml-2 font-mono text-[11px] text-faint">type {docType}</span>}
                </div>
                {filteredHits.map((h) => (
                  <DocumentSearchResultCard
                    key={h.chunk_id}
                    hit={h}
                    selected={selectedHit?.chunk_id === h.chunk_id}
                    onOpen={openDocument}
                  />
                ))}
              </div>
            )}
          </div>

          <div className="mt-3 flex flex-none items-center gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') run(input)
              }}
              placeholder="예) 반사파가 올라가면 무슨 문제인가?"
              className="h-10 min-w-0 flex-1 rounded-lg border border-field-line bg-white px-3.5 text-[13px] text-ink placeholder:text-faint"
            />
            <Button onClick={() => run(input)} disabled={loading || !input.trim()}>
              {loading && (
                <span
                  className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white"
                  aria-hidden="true"
                />
              )}
              {loading ? '검색 중…' : '검색'}
            </Button>
          </div>
        </div>
      </div>

      <DocumentDetailDrawer
        open={detailOpen}
        hit={selectedHit}
        detail={documentDetail}
        loading={detailLoading}
        error={detailError}
        onClose={closeDocument}
        onRetry={() => selectedHit && openDocument(selectedHit)}
        onNavigateChunk={navigateDetailChunk}
      />
    </div>
  )
}

export default DocumentsPage
