function RunRagEvidenceTab({ hits }) {
  if (hits.length === 0) {
    return (
      <div className="rounded-[10px] border-[1.5px] border-dashed border-dash-line p-8 text-center text-[12.5px] text-g2">
        이 실행과 연결된 근거 문서가 없습니다
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {hits.slice(0, 3).map((h) => (
        <div key={h.chunk_id} className="rounded-[10px] border border-line p-4">
          <div className="flex items-center justify-between gap-3">
            <span className="font-mono text-[11.5px] font-bold text-blue">{h.document_id}</span>
            <span className="flex items-center gap-2">
              <span className="h-[6px] w-[70px] overflow-hidden rounded-full bg-cell-line">
                <span className="block h-full rounded-full bg-blue" style={{ width: `${Math.round(h.score * 100)}%` }} />
              </span>
              <span className="font-mono text-[11px] font-bold text-ink">{h.score.toFixed(2)}</span>
            </span>
          </div>
          <div className="mt-1.5 text-[12.5px] font-bold text-ink">{h.section}</div>
          {h.content && <div className="mt-1.5 text-[12px] leading-[1.65] text-g1">{h.content}</div>}
        </div>
      ))}
    </div>
  )
}

export default RunRagEvidenceTab
