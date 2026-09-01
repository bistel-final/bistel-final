import { Link } from 'react-router-dom'

function RunRagEvidenceTab({ hits, diagnosis = null, compact = false }) {
  if (hits.length === 0) {
    return (
      <div className="rounded-[10px] border-[1.5px] border-dashed border-dash-line p-8 text-center text-[12.5px] text-g2">
        이 실행과 연결된 근거 문서가 없습니다
      </div>
    )
  }

  const interpretation = diagnosis?.status === 'AVAILABLE'
    ? diagnosis.evidence_synthesis?.trim()
    : ''

  return (
    <div className="flex flex-col gap-3" data-testid="agent-rag-evidence">
      <div className="rounded-[10px] border border-tint-blue-line bg-tint-blue px-4 py-3.5">
        <div className="flex items-center justify-between gap-3">
          <strong className="text-[11px] font-extrabold text-blue-hover">AI 근거 해석</strong>
          <span className="rounded-md border border-tint-blue-line bg-white px-2 py-0.5 text-[10px] font-bold text-blue">
            전체 근거 종합
          </span>
        </div>
        <div className="mt-2 text-[12.5px] font-semibold leading-6 text-ink">
          {interpretation || '이 실행에는 문서 근거를 종합한 AI 해석이 제공되지 않았습니다.'}
        </div>
      </div>

      {hits.map((hit, index) => (
        <div key={hit.chunk_id} className={`rounded-[10px] border border-line ${compact ? 'p-3' : 'p-4'}`}>
          <div className="flex items-center justify-between gap-3">
            <span className="min-w-0 truncate text-[12px] font-extrabold text-navy">
              {hit.title || hit.document_id || `문서 근거 ${index + 1}`}
            </span>
            {typeof hit.score === 'number' && (
              <span className="flex items-center gap-2">
                <span className="h-[6px] w-[70px] overflow-hidden rounded-full bg-cell-line">
                  <span className="block h-full rounded-full bg-blue" style={{ width: `${Math.round(hit.score * 100)}%` }} />
                </span>
                <span className="font-mono text-[11px] font-bold text-ink">{hit.score.toFixed(2)}</span>
              </span>
            )}
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5 text-[10px] font-bold text-g2">
            {hit.document_id && <span className="rounded-md bg-soft px-2 py-1">문서 {hit.document_id}</span>}
            {hit.section && <span className="rounded-md bg-soft px-2 py-1">위치 {hit.section}</span>}
          </div>
          {hit.content && <div className="mt-2 text-[12px] leading-[1.65] text-g1">{hit.content}</div>}
          {hit.href && (
            <div className="mt-3 text-right">
              <Link to={hit.href} className="text-[12px] font-bold text-blue">
                문서 원문 보기 →
              </Link>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

export default RunRagEvidenceTab
