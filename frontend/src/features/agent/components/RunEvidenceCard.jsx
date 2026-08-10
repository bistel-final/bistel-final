import Badge from '../../../shared/components/ui/Badge.jsx'
import { DashedCard } from '../../../shared/components/ui/Card.jsx'

// 좌측 3px 색 보더 — fdc 토큰 5색만 허용
const BORDER = {
  red: 'border-l-red',
  blue: 'border-l-blue',
  green: 'border-l-green',
  navy: 'border-l-navy',
  amber: 'border-l-amber',
}

// 근거 카드 항목 껍데기 — 제목/부제 · 좌측 미니 차트(DashedCard) · 우측 250px 「읽는 법」
function RunEvidenceCard({ color = 'red', title, sub, read, tag, tagVariant = 't-navy', children }) {
  return (
    <div className={`rounded-lg border border-line border-l-[3px] px-[18px] py-4 ${BORDER[color] ?? BORDER.red}`}>
      <div className="mb-3 flex items-baseline gap-3.5">
        <span className="text-[13.5px] font-extrabold text-navy">⊙ {title}</span>
        <span className="text-[11.5px] text-g1">{sub}</span>
      </div>
      <div className="flex items-stretch gap-4">
        <DashedCard className="flex flex-1 items-center px-3.5 py-2.5">{children}</DashedCard>
        <div className="w-[250px] flex-none rounded-lg border border-line bg-soft p-3.5">
          <div className="mb-2 text-[11px] font-extrabold text-navy">읽는 법</div>
          <div className="text-xs leading-[1.6] text-ink">{read}</div>
          {tag && (
            <div className="mt-2.5">
              <Badge variant={tagVariant}>{tag}</Badge>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default RunEvidenceCard
