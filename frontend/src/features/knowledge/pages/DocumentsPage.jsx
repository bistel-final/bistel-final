import { useEffect, useRef, useState } from 'react'
import { getDocument, searchDocuments } from '../../../shared/api/knowledge.js'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import MarkdownContent from '../../../shared/components/MarkdownContent.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card, CardHeader, DashedCard } from '../../../shared/components/ui/Card.jsx'
import { DOC_CHIPS, DOC_FILTERS } from '../mock/documents.js'

const ALL_MODELS = '전체'
const HISTORY_KEY = 'bistel.documents.searchHistory'
const MAX_HISTORY = 8

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
  const detailBodyRef = useRef(null)
  const selectedChunkRef = useRef(null)

  useEffect(() => {
    if (!detailOpen || !documentDetail || !detailBodyRef.current || !selectedChunkRef.current) return
    const body = detailBodyRef.current
    const target = selectedChunkRef.current
    body.scrollTop = target.offsetTop - body.clientHeight / 2 + target.clientHeight / 2
  }, [detailOpen, documentDetail, selectedHit])

  const run = (query) => {
    const q = query.trim()
    if (!q || loading) return
    setLoading(true)
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
    setSelectedHit(hit)
    setDetailOpen(true)
    setDetailLoading(true)
    setDetailError(null)
    getDocument(hit.document_id)
      .then((detail) => {
        setDocumentDetail(detail)
      })
      .catch((e) => {
        setDocumentDetail(null)
        setDetailError(e.message)
      })
      .finally(() => setDetailLoading(false))
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
        <Card className="flex w-[280px] flex-none flex-col">
          <CardHeader title="추천 질의" />
          <div className="flex flex-col gap-2 px-4">
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
                  <div
                    key={h.chunk_id}
                    role="button"
                    tabIndex={0}
                    onClick={() => openDocument(h)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        openDocument(h)
                      }
                    }}
                    className="cursor-pointer"
                  >
                    <Card
                      className={`px-5 py-4 transition hover:border-blue hover:bg-tint-blue ${
                        selectedHit?.chunk_id === h.chunk_id ? 'border-blue bg-row-sel' : ''
                      }`}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-[13.5px] font-extrabold text-ink">{h.section ?? h.title}</span>
                        <span className="flex flex-none items-center gap-2 font-mono text-[11px] font-bold text-blue">
                          {h.document_id}
                          <span className="text-g2">·</span>
                          {h.score.toFixed(2)}
                        </span>
                      </div>
                      <MarkdownContent content={h.content} className="mt-2 text-[12.5px] text-g1" />
                      <div className="mt-2 flex items-center justify-between gap-3">
                        {h.model_code ? (
                          <div className="font-mono text-[10.5px] text-faint">모델 {h.model_code}</div>
                        ) : (
                          <div />
                        )}
                        <div className="text-[11px] font-bold text-blue">상세 보기 ›</div>
                      </div>
                    </Card>
                  </div>
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

      <aside
        className={`absolute bottom-0 right-0 top-16 z-20 flex w-[840px] max-w-[calc(100%-296px)] flex-col border-l border-line bg-white shadow-2xl transition-transform duration-200 ease-out ${
          detailOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
        aria-hidden={!detailOpen}
      >
        <div className="flex flex-none items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <div className="text-[16px] font-extrabold text-ink">{documentDetail?.title ?? selectedHit?.title ?? '문서 상세'}</div>
            <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[11px] text-g2">
              {selectedHit?.document_id && <span>{selectedHit.document_id}</span>}
              {documentDetail?.model_code && (
                <span className="rounded bg-tint-blue px-2 py-0.5 font-bold text-blue">{documentDetail.model_code}</span>
              )}
              {documentDetail?.doc_type && (
                <span className="rounded bg-soft px-2 py-0.5 font-bold text-g1">{documentDetail.doc_type}</span>
              )}
              {documentDetail?.version && <span>{documentDetail.version}</span>}
            </div>
          </div>
          <button
            type="button"
            onClick={closeDocument}
            className="flex h-8 w-8 flex-none items-center justify-center rounded-lg text-[18px] text-g2 hover:bg-soft hover:text-ink"
            aria-label="문서 상세 닫기"
          >
            ×
          </button>
        </div>

        <div ref={detailBodyRef} className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {detailLoading ? (
            <DashedCard className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
              <div className="text-[13px] font-extrabold text-ink">문서 내용을 불러오는 중…</div>
              <div className="text-[12px] text-g1">선택한 검색 결과의 전체 청크를 조회하고 있습니다.</div>
            </DashedCard>
          ) : detailError ? (
            <ErrorState title="문서 상세를 불러오지 못했습니다" detail={detailError} onRetry={() => selectedHit && openDocument(selectedHit)} />
          ) : !documentDetail ? (
            <DashedCard className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
              <div className="text-[13px] font-extrabold text-ink">문서를 선택해 주세요</div>
            </DashedCard>
          ) : (
            <div className="flex flex-col gap-3">
              {documentDetail.chunks.map((chunk) => {
                const selected = chunk.chunk_id === selectedHit?.chunk_id
                return (
                  <section
                    key={chunk.chunk_id}
                    ref={selected ? selectedChunkRef : null}
                    className={`rounded-xl border p-4 ${
                      selected ? 'border-blue bg-tint-blue shadow-sm' : 'border-line bg-white'
                    }`}
                  >
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="font-mono text-[10.5px] font-bold text-g2">chunk {chunk.chunk_seq}</div>
                        {chunk.section_title && (
                          <div className="mt-0.5 truncate text-[13px] font-extrabold text-ink" title={chunk.section_title}>
                            {chunk.section_title}
                          </div>
                        )}
                      </div>
                      {selected && (
                        <span className="flex-none rounded-full bg-blue px-2.5 py-1 text-[10px] font-bold text-white">선택 청크</span>
                      )}
                    </div>
                    <MarkdownContent content={chunk.content} className="text-[12.5px] text-g1" />
                  </section>
                )
              })}
            </div>
          )}
        </div>
      </aside>
    </div>
  )
}

export default DocumentsPage
