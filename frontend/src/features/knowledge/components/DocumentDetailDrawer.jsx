import { useEffect, useMemo, useRef, useState } from 'react'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import MarkdownContent from '../../../shared/components/MarkdownContent.jsx'
import { DashedCard } from '../../../shared/components/ui/Card.jsx'

function DocumentDetailDrawer({
  open,
  hit,
  detail,
  loading,
  error,
  onClose,
  onRetry,
  onNavigateChunk,
  wide = false,
}) {
  const detailBodyRef = useRef(null)
  const selectedChunkRef = useRef(null)
  const chunkRefs = useRef({})
  const [activeChunkId, setActiveChunkId] = useState(null)
  const chunks = useMemo(() => detail?.chunks ?? [], [detail])
  const selectedChunkId = hit?.chunk_id ?? null
  const activeChunk = useMemo(
    () =>
      chunks.find((chunk) => chunk.chunk_id === activeChunkId) ??
      chunks.find((chunk) => chunk.chunk_id === selectedChunkId) ??
      null,
    [activeChunkId, chunks, selectedChunkId],
  )

  useEffect(() => {
    if (!open || !detail || !detailBodyRef.current || !selectedChunkRef.current) return

    const body = detailBodyRef.current
    const target = selectedChunkRef.current
    body.scrollTop = target.offsetTop - body.clientHeight / 2 + target.clientHeight / 2
  }, [open, detail, hit])

  useEffect(() => {
    const body = detailBodyRef.current
    if (!open || !body || !chunks.length) return undefined

    const updateActiveChunk = () => {
      const viewportTop = body.scrollTop + 112
      const currentChunk =
        chunks
          .map((chunk) => ({ chunk, top: chunkRefs.current[chunk.chunk_id]?.offsetTop ?? Number.POSITIVE_INFINITY }))
          .filter((item) => Number.isFinite(item.top) && item.top <= viewportTop)
          .at(-1)?.chunk ?? chunks[0]
      setActiveChunkId((previous) => (previous === currentChunk.chunk_id ? previous : currentChunk.chunk_id))
    }

    body.addEventListener('scroll', updateActiveChunk, { passive: true })
    updateActiveChunk()
    return () => body.removeEventListener('scroll', updateActiveChunk)
  }, [chunks, open])

  const navigateChunk = (chunk) => {
    const body = detailBodyRef.current
    const target = chunkRefs.current[chunk.chunk_id]
    if (body && target) body.scrollTop = target.offsetTop - 96
    setActiveChunkId(chunk.chunk_id)
    onNavigateChunk?.(chunk)
  }

  return (
    <aside
      className={`absolute bottom-0 right-0 top-16 z-20 flex flex-col border-l border-line bg-white shadow-2xl transition-transform duration-200 ease-out ${
        wide ? 'left-[296px]' : 'w-[1008px] max-w-[calc(100%-296px)]'
      } ${
        open ? 'translate-x-0' : 'translate-x-full'
      }`}
      aria-hidden={!open}
    >
      <div className="flex flex-none items-start justify-between gap-4 border-b border-line px-5 py-4">
        <div className="min-w-0">
          <div className="text-[16px] font-extrabold text-ink">{detail?.title ?? hit?.title ?? '문서 상세'}</div>
          <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[11px] text-g2">
            {hit?.document_id && <span>{hit.document_id}</span>}
            {detail?.model_code && <span className="rounded bg-tint-blue px-2 py-0.5 font-bold text-blue">{detail.model_code}</span>}
            {detail?.doc_type && <span className="rounded bg-soft px-2 py-0.5 font-bold text-g1">{detail.doc_type}</span>}
            {detail?.version && <span>{detail.version}</span>}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="flex h-8 w-8 flex-none items-center justify-center rounded-lg text-[18px] text-g2 hover:bg-soft hover:text-ink"
          aria-label="문서 상세 닫기"
        >
          ×
        </button>
      </div>

      <div ref={detailBodyRef} className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {loading ? (
          <DashedCard className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
            <div className="text-[13px] font-extrabold text-ink">문서 내용을 불러오는 중…</div>
            <div className="text-[12px] text-g1">선택한 검색 결과의 전체 청크를 조회하고 있습니다.</div>
          </DashedCard>
        ) : error ? (
          <ErrorState title="문서 상세를 불러오지 못했습니다" detail={error} onRetry={onRetry} />
        ) : !detail ? (
          <DashedCard className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
            <div className="text-[13px] font-extrabold text-ink">문서를 선택해 주세요</div>
          </DashedCard>
        ) : (
          <div className="grid min-h-0 grid-cols-[230px_minmax(0,1fr)] gap-4">
            <nav className="sticky top-0 max-h-[calc(100vh-210px)] overflow-y-auto rounded-[10px] border border-line bg-soft p-3">
              <div className="mb-3">
                <div className="text-[11px] font-extrabold text-g2">문서 목차</div>
                <div className="mt-1 font-mono text-[10px] text-faint">
                  {chunks.length}개 청크
                  {activeChunk ? ` · 현재 ${activeChunk.chunk_seq}` : ''}
                </div>
              </div>
              <div className="mb-3 grid grid-cols-2 gap-1.5 text-[10.5px]">
                <div className="rounded-md border border-cell-line bg-white px-2 py-1.5">
                  <div className="text-faint">유형</div>
                  <div className="mt-0.5 truncate font-bold text-ink">{detail.doc_type ?? '미지정'}</div>
                </div>
                <div className="rounded-md border border-cell-line bg-white px-2 py-1.5">
                  <div className="text-faint">모델</div>
                  <div className="mt-0.5 truncate font-bold text-ink">{detail.model_code ?? 'COMMON'}</div>
                </div>
                <div className="col-span-2 rounded-md border border-cell-line bg-white px-2 py-1.5">
                  <div className="text-faint">버전</div>
                  <div className="mt-0.5 truncate font-bold text-ink">{detail.version ?? '미지정'}</div>
                </div>
              </div>
              <div className="flex flex-col gap-1">
                {chunks.map((chunk) => {
                  const selected = chunk.chunk_id === selectedChunkId
                  const active = chunk.chunk_id === activeChunk?.chunk_id
                  return (
                    <button
                      key={chunk.chunk_id}
                      type="button"
                      onClick={() => navigateChunk(chunk)}
                      className={`cursor-pointer rounded-md px-2.5 py-2 text-left transition ${
                        active ? 'bg-white shadow-sm ring-1 ring-blue/40' : 'hover:bg-white'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="font-mono text-[10px] font-bold text-g2">chunk {chunk.chunk_seq}</div>
                        {selected && <span className="rounded-full bg-blue px-1.5 py-0.5 text-[9.5px] font-bold text-white">선택</span>}
                      </div>
                      <div className="mt-0.5 line-clamp-2 text-[11.5px] font-bold text-ink">
                        {chunk.section_title ?? chunk.chunk_id}
                      </div>
                    </button>
                  )
                })}
              </div>
            </nav>

            <div className="flex min-w-0 flex-col gap-3">
              {chunks.map((chunk) => {
                const selected = chunk.chunk_id === selectedChunkId
                const active = chunk.chunk_id === activeChunk?.chunk_id
                return (
                  <section
                    key={chunk.chunk_id}
                    ref={(node) => {
                      if (node) chunkRefs.current[chunk.chunk_id] = node
                      if (selected) selectedChunkRef.current = node
                    }}
                    className={`rounded-[10px] border p-4 ${
                      selected ? 'border-blue bg-tint-blue shadow-sm' : active ? 'border-blue/60 bg-white' : 'border-line bg-white'
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
                      <div className="flex flex-none items-center gap-1.5">
                        {active && <span className="rounded-full bg-soft px-2.5 py-1 text-[10px] font-bold text-g1">현재 위치</span>}
                        {selected && <span className="rounded-full bg-blue px-2.5 py-1 text-[10px] font-bold text-white">선택 청크</span>}
                      </div>
                    </div>
                    <MarkdownContent content={chunk.content} className="text-[12.5px] text-g1" />
                  </section>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </aside>
  )
}

export default DocumentDetailDrawer
