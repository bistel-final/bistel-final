import { useCallback, useEffect, useState } from 'react'
import { getChamberRelations, searchDocuments } from '../../../shared/api/knowledge.js'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import { FilterField, FilterSelect } from '../../../shared/components/ui/FilterField.jsx'
import { DOC_CHIPS, DOC_FILTERS } from '../mock/documents.js'

const CHAMBER_OPTIONS = ['PHO-01-C1', 'PHO-01-C2', 'ETC-01-C1', 'ETC-01-C2']

function NodeList({ title, items }) {
  return (
    <div className="rounded-lg border border-line bg-soft p-3.5">
      <div className="mb-2 text-xs font-bold text-g1">{title}</div>
      {items.length ? (
        <div className="flex flex-wrap gap-2">
          {items.map((item) => (
            <span key={item.equipment_id ?? item.chamber_id} className="rounded-md bg-white px-2.5 py-1.5 font-mono text-xs font-bold text-navy">
              {item.equipment_id ?? item.chamber_id}
            </span>
          ))}
        </div>
      ) : (
        <span className="text-xs text-g2">없음</span>
      )}
    </div>
  )
}

function KnowledgePage() {
  const [chamberId, setChamberId] = useState(CHAMBER_OPTIONS[0])
  const [relations, setRelations] = useState(null)
  const [relationError, setRelationError] = useState(null)
  const [query, setQuery] = useState('')
  const [modelCode, setModelCode] = useState('전체')
  const [results, setResults] = useState(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState(null)

  const loadRelations = useCallback(() => {
    getChamberRelations(chamberId)
      .then((response) => {
        setRelations({ chamberId, value: response })
        setRelationError(null)
      })
      .catch((requestError) => setRelationError(requestError.message))
  }, [chamberId])

  useEffect(() => {
    loadRelations()
  }, [loadRelations])

  const search = (nextQuery = query) => {
    const normalized = nextQuery.trim()
    if (!normalized) {
      setResults(null)
      return
    }
    setSearching(true)
    searchDocuments({ query: normalized, model_code: modelCode, top_k: 4 })
      .then((response) => {
        setResults(response)
        setSearchError(null)
      })
      .catch((requestError) => setSearchError(requestError.message))
      .finally(() => setSearching(false))
  }

  const chooseExample = (value) => {
    setQuery(value)
    search(value)
  }

  if (relationError && !relations) return <ErrorState detail={relationError} onRetry={loadRelations} />
  if (relations?.chamberId !== chamberId) return <LoadingState message="설비 관계를 불러오는 중…" />

  const relation = relations.value
  if (!relation) return <EmptyState title="선택한 챔버의 관계 정보가 없습니다" description={chamberId} />

  return (
    <div className="flex animate-[om-fadein_.3s_ease-out] flex-col gap-4">
      <div className="flex min-h-16 items-center justify-between">
        <div className="text-[22px] font-extrabold text-navy">관계·문서 근거</div>
        <FilterField label="기준 챔버">
          <FilterSelect value={chamberId} options={CHAMBER_OPTIONS} mono onChange={setChamberId} />
        </FilterField>
      </div>

      <div className="grid grid-cols-[3fr_2fr] items-start gap-4">
        <Card>
          <CardHeader title="장비 관계" note="선택한 단일 챔버 기준" />
          <div className="flex flex-col gap-3 px-5 pb-5">
            <div className="rounded-xl bg-navy px-5 py-4 text-white">
              <div className="font-mono text-lg font-extrabold">{relation.chamber.chamber_id}</div>
              <div className="mt-1 text-xs text-sidebar-dim">
                {relation.equipment.equipment_id} · {relation.equipment.model_code ?? '모델 미제공'} ·{' '}
                {relation.area?.area_id ?? 'AREA 미제공'}
              </div>
            </div>
            <NodeList title="형제 챔버" items={relation.sibling_chambers ?? []} />
            <NodeList title="상류 장비" items={relation.upstream ?? []} />
            <NodeList title="하류 장비" items={relation.downstream ?? []} />
            <div className="rounded-lg border border-line bg-soft p-3.5 text-xs leading-5 text-g1">
              공정 단계 <span className="font-mono font-bold text-navy">{relation.step?.step_id ?? '—'}</span>
              {relation.step?.layer ? ` · Layer ${relation.step.layer}` : ''}
            </div>
          </div>
        </Card>

        <Card>
          <CardHeader title="문서 검색" note="pgvector top_k=4" />
          <div className="flex flex-col gap-3 px-5 pb-5">
            <div className="flex gap-2">
              <input
                type="text"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => event.key === 'Enter' && search()}
                placeholder="질문 또는 키워드 입력"
                className="min-w-0 flex-1 rounded-lg border border-line px-3 py-2.5 text-sm text-ink"
              />
              <button type="button" onClick={() => search()} className="rounded-lg bg-blue px-4 text-sm font-bold text-white">
                {searching ? '검색 중…' : '검색'}
              </button>
            </div>

            <div className="flex gap-1.5">
              {DOC_FILTERS.map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setModelCode(value)}
                  className={`rounded-lg border px-3 py-1.5 font-mono text-xs font-bold ${
                    modelCode === value ? 'border-blue bg-blue text-white' : 'border-line bg-white text-g1'
                  }`}
                >
                  {value}
                </button>
              ))}
            </div>

            <div className="flex flex-wrap gap-1.5">
              {DOC_CHIPS.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => chooseExample(example)}
                  className="rounded-full border border-tint-blue-line bg-tint-blue px-3 py-1.5 text-xs font-bold text-blue"
                >
                  {example}
                </button>
              ))}
            </div>

            {searchError && <ErrorState title="문서 검색에 실패했습니다" detail={searchError} onRetry={() => search()} />}
            {!searchError && !results && <EmptyState title="문서 검색어를 입력해 주세요" />}
            {!searchError && results && results.hits.length === 0 && (
              <EmptyState title="검색 결과가 없습니다" description="검색어나 모델 필터를 변경해 주세요." />
            )}
            {!searchError && results?.hits.length > 0 && (
              <div className="flex flex-col gap-2.5">
                {results.hits.map((hit) => (
                  <article key={hit.chunk_id} className="rounded-lg border border-line p-3.5">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs font-extrabold text-navy">{hit.title}</span>
                      {hit.model_code && <span className="rounded bg-soft px-2 py-0.5 font-mono text-[10px] text-g1">{hit.model_code}</span>}
                      <span className="ml-auto font-mono text-[10px] text-g2">{hit.score.toFixed(3)}</span>
                    </div>
                    {hit.section && <div className="mt-1.5 text-xs font-bold text-ink">{hit.section}</div>}
                    <div className="mt-2 line-clamp-4 text-xs leading-5 text-g1">{hit.content}</div>
                  </article>
                ))}
              </div>
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}

export default KnowledgePage
