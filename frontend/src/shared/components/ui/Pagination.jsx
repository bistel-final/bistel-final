// fdc.css .pager / .pager-info / .pg(.on)
function Pagination({ page, pageCount, total, rangeLabel, onPage }) {
  const pages = Array.from({ length: pageCount }, (_, i) => i + 1)
  return (
    <div className="flex items-center justify-between px-5 py-3.5">
      <span className="font-mono text-xs text-g1">{rangeLabel ?? `${total}건`}</span>
      <div className="flex gap-1.5">
        <button
          type="button"
          onClick={() => onPage(Math.max(1, page - 1))}
          className="inline-flex h-7 min-w-7 cursor-pointer items-center justify-center rounded-md border border-line bg-white font-mono text-xs text-g1"
        >
          ‹
        </button>
        {pages.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => onPage(p)}
            className={`inline-flex h-7 min-w-7 cursor-pointer items-center justify-center rounded-md border font-mono text-xs ${
              p === page ? 'border-blue bg-blue font-bold text-white' : 'border-line bg-white text-g1'
            }`}
          >
            {p}
          </button>
        ))}
        <button
          type="button"
          onClick={() => onPage(Math.min(pageCount, page + 1))}
          className="inline-flex h-7 min-w-7 cursor-pointer items-center justify-center rounded-md border border-line bg-white font-mono text-xs text-g1"
        >
          ›
        </button>
      </div>
    </div>
  )
}

export default Pagination
