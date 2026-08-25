import { useState } from 'react'
import { searchDocuments } from '../../../shared/api/knowledge.js'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card, CardHeader, DashedCard } from '../../../shared/components/ui/Card.jsx'
import { DOC_CHIPS, DOC_FILTERS } from '../mock/documents.js'

const ALL_MODELS = '전체'

// 문서 검색 — 라이트 시안 4번
// 좌 280px: 추천 질의 + 검색 기록(세션 state) / 우측: 결과 카드 / 하단: 입력 + [검색]
function DocumentsPage() {
  const [input, setInput] = useState('')
  const [result, setResult] = useState(null) // { query, hits }
  const [modelCode, setModelCode] = useState(ALL_MODELS)
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const run = (query) => {
    const q = query.trim()
    if (!q || loading) return
    setLoading(true)
    searchDocuments({
      query: q,
      model_code: modelCode === ALL_MODELS ? undefined : modelCode,
      top_k: 4,
    })
      .then((res) => {
        setResult({ ...res, model_code: modelCode })
        setHistory((prev) => [q, ...prev.filter((h) => h !== q)].slice(0, 8))
        setInput('')
        setError(null)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
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
    <div className="flex h-[calc(100vh-72px)] animate-[om-fadein_.3s_ease-out] flex-col">
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
                  <Card key={h.chunk_id} className="px-5 py-4">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-[13.5px] font-extrabold text-ink">{h.section ?? h.title}</span>
                      <span className="flex flex-none items-center gap-2 font-mono text-[11px] font-bold text-blue">
                        {h.document_id}
                        <span className="text-g2">·</span>
                        {h.score.toFixed(2)}
                      </span>
                    </div>
                    {h.content && <div className="mt-2 text-[12.5px] leading-[1.7] text-g1">{h.content}</div>}
                    {h.model_code && <div className="mt-2 font-mono text-[10.5px] text-faint">모델 {h.model_code}</div>}
                  </Card>
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
    </div>
  )
}

export default DocumentsPage
