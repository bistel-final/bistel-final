import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getDocument, searchDocuments } from '../../../shared/api/knowledge.js'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card, CardHeader, DashedCard } from '../../../shared/components/ui/Card.jsx'
import DocumentDetailDrawer from '../components/DocumentDetailDrawer.jsx'
import DocumentSearchResultCard from '../components/DocumentSearchResultCard.jsx'
import { DOC_CHIPS, DOC_FILTERS } from '../mock/documents.js'

const ALL_MODELS = '전체'
const HISTORY_KEY = 'bistel.documents.searchHistory'
const MAX_HISTORY = 8
const DOCUMENT_LIBRARY = [
  {
    group: 'Troubleshooting',
    items: [
      {
        document_id: 'DOC-TROUBLE-FDC',
        title: 'FDC Fault Guide',
        meta: 'COMMON · 조치 기준',
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
      },
      {
        document_id: 'DOC-SPEC-ET7500',
        title: 'ET-7500 Dry Etcher',
        meta: 'ET-7500 · Etch',
      },
    ],
  },
]

function loadSearchHistory() {
  try {
    const value = JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]')
    return Array.isArray(value) ? value.filter((item) => typeof item === 'string').slice(0, MAX_HISTORY) : []
  } catch {
    return []
  }
}

function saveSearchHistory(history) {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history))
}

// 문서 검색 — 라이트 시안 4번
// 좌 280px: 추천 질의 + 검색 기록(localStorage) / 우측: 결과 카드 / 하단: 입력 + [검색]
function DocumentsPage() {
  const [searchParams] = useSearchParams()
  const documentRequestRef = useRef(0)
  const [input, setInput] = useState('')
  const [result, setResult] = useState(null) // { query, hits }
  const [modelCode, setModelCode] = useState(ALL_MODELS)
  const [history, setHistory] = useState(loadSearchHistory)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [selectedHit, setSelectedHit] = useState(null)
  const [documentDetail, setDocumentDetail] = useState(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState(null)
  const deepLinkKey = `${searchParams.get('document_id') ?? ''}:${searchParams.get('chunk_id') ?? ''}`

  useEffect(() => {
    const documentId = searchParams.get('document_id')
    const chunkId = searchParams.get('chunk_id')
    if (!documentId || !chunkId) return
    const requestToken = ++documentRequestRef.current
    Promise.resolve().then(() => {
      if (documentRequestRef.current !== requestToken) return
      setSelectedHit({ document_id: documentId, chunk_id: chunkId, title: documentId })
      setDetailOpen(true)
      setDetailLoading(true)
      setDetailError(null)
      return getDocument(documentId)
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
  }, [deepLinkKey, searchParams])

  const run = (query) => {
    const q = query.trim()
    if (!q || loading) return
    setLoading(true)
    documentRequestRef.current += 1
    setDetailOpen(false)
    setSelectedHit(null)
    setDocumentDetail(null)
    setDetailError(null)
    searchDocuments({
      query: q,
      model_code: modelCode === ALL_MODELS ? undefined : modelCode,
      top_k: 4,
    })
      .then((res) => {
        setResult({ ...res, model_code: modelCode })
        setHistory((prev) => {
          const next = [q, ...prev.filter((h) => h !== q)].slice(0, MAX_HISTORY)
          saveSearchHistory(next)
          return next
        })
        setInput('')
        setError(null)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  const openDocument = (hit) => {
    const requestToken = ++documentRequestRef.current
    setSelectedHit(hit)
    setDetailOpen(true)
    setDetailLoading(true)
    setDetailError(null)
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
  }

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
        <Card className="flex w-[280px] flex-none flex-col overflow-hidden">
          <CardHeader title="추천 질의" />
          <div className="flex flex-col gap-2 px-4 pb-1">
            {DOC_CHIPS.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => run(q)}
                className="cursor-pointer rounded-lg border border-tint-blue-line bg-tint-blue px-3 py-2 text-left text-[12px] font-semibold text-blue-hover hover:bg-white"
              >
                {q}
              </button>
            ))}
          </div>

          <div className="mt-3 border-t border-cell-line px-4 pb-3 pt-3">
            <div className="mb-2 text-[11px] font-bold text-g2">문서 라이브러리</div>
            <div className="flex flex-col gap-3">
              {DOCUMENT_LIBRARY.map((group) => (
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

          <div className="mt-4 border-t border-cell-line px-4 pb-4 pt-3">
            <div className="mb-2 text-[11px] font-bold text-g2">검색 기록</div>
            {history.length === 0 ? (
              <div className="text-[11.5px] text-faint">아직 검색 기록이 없습니다</div>
            ) : (
              <div className="flex flex-col gap-1.5">
                {history.map((q) => (
                  <button
                    key={q}
                    type="button"
                    onClick={() => run(q)}
                    className="cursor-pointer truncate text-left text-[12px] text-g1 hover:text-blue"
                    title={q}
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}
          </div>
        </Card>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto">
            {!result ? (
              <DashedCard className="flex h-full flex-col items-center justify-center gap-2 p-10 text-center">
                <div className="text-[14px] font-extrabold text-ink">무엇이 궁금하신가요?</div>
                <div className="text-[12px] text-g1">
                  왼쪽 추천 질의를 누르거나 아래 입력창에 질문을 입력해 주세요.
                </div>
              </DashedCard>
            ) : result.hits.length === 0 ? (
              <DashedCard className="flex h-full flex-col items-center justify-center gap-2 p-10 text-center">
                <div className="text-[14px] font-extrabold text-ink">검색 결과가 없습니다</div>
                <div className="text-[12px] text-g1">다른 표현으로 다시 검색해 주세요.</div>
              </DashedCard>
            ) : (
              <div className="flex flex-col gap-3">
                <div className="text-[12px] text-g1">
                  <span className="font-semibold text-ink">“{result.query}”</span> 검색 결과{' '}
                  <span className="font-mono">{result.count ?? result.hits.length}</span>건
                  <span className="ml-2 font-mono text-[11px] text-faint">model {result.model_code}</span>
                </div>
                {result.hits.map((h) => (
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
            <select
              value={modelCode}
              onChange={(e) => setModelCode(e.target.value)}
              className="h-10 w-[120px] rounded-lg border border-field-line bg-white px-3 font-mono text-[12px] font-bold text-ink"
            >
              {DOC_FILTERS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') run(input)
              }}
              placeholder="예) 반사파가 올라가면 무슨 문제인가"
              className="h-10 min-w-0 flex-1 rounded-lg border border-field-line bg-white px-3.5 text-[13px] text-ink placeholder:text-faint"
            />
            <Button onClick={() => run(input)} disabled={loading || !input.trim()}>
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
      />
    </div>
  )
}

export default DocumentsPage
