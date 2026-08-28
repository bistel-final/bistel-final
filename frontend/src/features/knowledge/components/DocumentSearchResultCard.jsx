import MarkdownContent from '../../../shared/components/MarkdownContent.jsx'
import { Card } from '../../../shared/components/ui/Card.jsx'

function DocumentSearchResultCard({ hit, selected, onOpen }) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onOpen(hit)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onOpen(hit)
        }
      }}
      className="cursor-pointer"
    >
      <Card
        className={`px-5 py-4 transition hover:border-blue hover:bg-tint-blue ${
          selected ? 'border-blue bg-row-sel' : ''
        }`}
      >
        <div className="flex items-center justify-between gap-3">
          <span className="text-[13.5px] font-extrabold text-ink">{hit.section ?? hit.title}</span>
          <span className="flex flex-none items-center gap-2 font-mono text-[11px] font-bold text-blue">
            {hit.document_id}
            <span className="text-g2">·</span>
            {hit.score.toFixed(2)}
          </span>
        </div>
        <MarkdownContent content={hit.content} className="mt-2 text-[12.5px] text-g1" />
        <div className="mt-2 flex items-center justify-between gap-3">
          {hit.model_code ? <div className="font-mono text-[10.5px] text-faint">모델 {hit.model_code}</div> : <div />}
          <div className="text-[11px] font-bold text-blue">상세 보기 ›</div>
        </div>
      </Card>
    </div>
  )
}

export default DocumentSearchResultCard
