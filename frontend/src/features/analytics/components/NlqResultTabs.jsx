// 결과 — 표 / 통계 / 차트 3개 탭 (디자인 v2 06)
// 차트 정보는 visualization.chart_type · x · y 를 우선 읽고, 없으면 기존 chart 필드로 폴백한다.
import Button from '../../../shared/components/ui/Button.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import HBar from '../../../shared/components/ui/HBar.jsx'
import KVGrid from '../../../shared/components/ui/KVGrid.jsx'
import { TH_CLS, TD_CLS, CELL_ID, CELL_MONO, rowClass } from '../../../shared/components/ui/statusStyles.js'

const TABS = [
  ['table', '표'],
  ['stats', '통계'],
  ['chart', '차트'],
]

// visualization.chart_type/x/y 우선, 없으면 def.chart + 첫/마지막 컬럼
function readViz(def) {
  const cols = def.cols ?? []
  const viz = def.visualization ?? {}
  const raw = viz.chart_type ?? def.chart ?? 'demote'
  const type = raw === 'bar' ? 'bar' : 'demote'
  const xIdx = cols.indexOf(viz.x) >= 0 ? cols.indexOf(viz.x) : 0
  const yIdx = cols.indexOf(viz.y) >= 0 ? cols.indexOf(viz.y) : Math.max(0, cols.length - 1)
  return { type, xIdx, yIdx }
}

// def.stats(실측 제공)가 있으면 그대로, 없으면 결과 행에서 계산 (창작 없음)
function buildStats(def, yIdx) {
  if (def.stats) return { rows: def.stats, derived: false }
  const rows = def.rows ?? []
  const values = rows.map((r) => r[yIdx])
  if (rows.length === 0 || values.some((v) => typeof v !== 'number' || !Number.isFinite(v))) return null
  const n = values.length
  const mean = values.reduce((a, b) => a + b, 0) / n
  const std = n > 1 ? Math.sqrt(values.reduce((a, b) => a + (b - mean) ** 2, 0) / (n - 1)) : null
  return {
    derived: true,
    rows: [
      ['count', String(n)],
      ['mean', mean.toFixed(1)],
      ['std', std === null ? '—' : std.toFixed(2)],
      ['min', String(Math.min(...values))],
      ['max', String(Math.max(...values))],
    ],
  }
}

function Footnote({ text }) {
  if (!text) return null
  return <div className="rounded-lg border border-line bg-soft px-4 py-3.5 text-xs text-g1">{text}</div>
}

function NlqResultTabs({ def, tab, onTab, sortAsc, onToggleSort, rows, footnote }) {
  const { type, xIdx, yIdx } = readViz(def)
  const stats = buildStats(def, yIdx)
  const viz = def.visualization
  const cols = def.cols ?? []
  // 바 폭은 항상 값/최대값 비율 (하드코딩 % 금지)
  const max = Math.max(0, ...rows.map((r) => Number(r[yIdx]) || 0))

  return (
    <Card className="animate-[om-fadein_.25s]">
      <CardHeader
        title="결과"
        note={
          <span className="font-mono">
            {(def.rows ?? []).length}행 · {def.lat.toLocaleString()}ms
          </span>
        }
      />

      <div className="flex items-center justify-between px-5 pb-4">
        <div className="flex gap-2">
          {TABS.map(([key, label]) => (
            <Button
              key={key}
              sm
              variant={tab === key ? 'primary' : 'outline'}
              style={{ minWidth: 60 }}
              onClick={() => onTab(key)}
            >
              {label}
            </Button>
          ))}
        </div>
        {viz && (
          <span className="font-mono text-[11px] text-g1">
            chart_type = {viz.chart_type} · x = {viz.x ?? '—'} · y = {viz.y}
          </span>
        )}
      </div>

      {tab === 'table' && (
        <div className="flex flex-col gap-3 px-5 pb-5">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                {cols.map((c, i) => {
                  const sortable = !def.noSort && cols.length > 1 && i === cols.length - 1
                  return (
                    <th
                      key={c}
                      onClick={sortable ? onToggleSort : undefined}
                      className={`${TH_CLS} font-mono ${sortable ? 'cursor-pointer' : ''}`}
                    >
                      {c}
                      {sortable && <span className="ml-1 text-[9px] text-g2">{sortAsc ? '▲' : '▼'}</span>}
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className={rowClass(i)}>
                  {r.map((v, j) => (
                    <td key={j} className={`${TD_CLS} ${j === 0 && r.length > 1 ? CELL_ID : CELL_MONO}`}>
                      {String(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <Footnote text={footnote} />
        </div>
      )}

      {tab === 'stats' && (
        <div className="flex flex-col gap-3.5 px-5 pb-5">
          {stats ? (
            <>
              <KVGrid items={stats.rows} />
              <div className="text-xs text-g1">
                std는 표본 표준편차(ddof=1) 기준 · {cols[yIdx] ?? '—'} 컬럼
                {stats.derived ? ' · 결과 행에서 계산' : ' · 실행 결과 제공값'}
              </div>
              <Footnote text={footnote} />
            </>
          ) : (
            <div className="rounded-lg border border-line bg-soft px-4 py-3.5 text-xs text-g1">
              수치형 집계 컬럼이 없어 요약 통계를 제공하지 않습니다 — 표 탭을 이용하세요
            </div>
          )}
        </div>
      )}

      {tab === 'chart' && (
        <div className="flex flex-col gap-[22px] px-6 pb-5 pt-1.5">
          {type === 'bar' ? (
            rows.map((r) => (
              <div key={String(r[xIdx])}>
                <div className="mb-2 font-mono text-xs font-bold text-navy">{String(r[xIdx])}</div>
                <div className="flex items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <HBar value={Number(r[yIdx]) || 0} max={max} />
                  </div>
                  <span className="w-[26px] font-mono text-xs font-bold text-navy">{String(r[yIdx])}</span>
                </div>
              </div>
            ))
          ) : (
            <div className="rounded-lg border border-line bg-soft px-4 py-3.5 text-xs text-g1">
              차트로 표현할 수 없는 결과 형태 — 표 탭을 이용하세요
            </div>
          )}
          <Footnote text={footnote} />
        </div>
      )}
    </Card>
  )
}

export default NlqResultTabs
