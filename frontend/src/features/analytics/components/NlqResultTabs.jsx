<<<<<<< Updated upstream
// 결과 — 표 / 통계 / 차트 3개 탭 (디자인 v2 06)
// 명세 AnalysisQueryResponse: rows 는 객체 배열이라 컬럼 접근은 row[columns[i]] 다.
// 차트는 visualization.chart_type · x · y 를 그대로 읽는다.
=======
>>>>>>> Stashed changes
import Button from '../../../shared/components/ui/Button.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import HBar from '../../../shared/components/ui/HBar.jsx'
import KVGrid from '../../../shared/components/ui/KVGrid.jsx'
import { CELL_ID, CELL_MONO, TD_CLS, TH_CLS, rowClass } from '../../../shared/components/ui/statusStyles.js'

const TABS = [
  ['table', '표'],
  ['stats', '통계'],
  ['chart', '차트'],
]

<<<<<<< Updated upstream
// visualization.chart_type/x/y — 컬럼 목록에 없는 이름은 첫/마지막 컬럼으로 폴백한다
function readViz(def) {
  const columns = def.columns ?? []
  const viz = def.visualization ?? {}
  const type = viz.chart_type === 'bar' ? 'bar' : 'demote'
  const x = columns.includes(viz.x) ? viz.x : columns[0]
  const y = columns.includes(viz.y) ? viz.y : columns[columns.length - 1]
  return { type, x, y }
}

// metric = {type, column, p} → "p95(cd_aei)" 형태의 한 줄 표기
function metricLabel(metric) {
  if (!metric?.type) return '—'
  const p = metric.p == null ? '' : ` p=${metric.p}`
  return `${metric.type}${metric.column ? `(${metric.column})` : ''}${p}`
}

// metric_result: 스칼라 또는 [{group:{...}, value}] — 둘 다 KVGrid 항목으로 편다
function metricItems(def) {
  const r = def.metric_result
  if (r == null) return []
  if (Array.isArray(r))
    return r.map((m) => [Object.values(m.group ?? {}).join(' · ') || '—', String(m.value)])
  return [[metricLabel(def.metric), String(r)]]
}

// y 컬럼이 수치형이면 결과 행에서 요약 통계를 계산한다 (창작 없음 — 근거를 함께 표기)
function buildStats(def, y) {
  const rows = def.rows ?? []
  const values = rows.map((r) => r[y])
  if (rows.length === 0 || values.some((v) => typeof v !== 'number' || !Number.isFinite(v))) return null
  const n = values.length
  const mean = values.reduce((a, b) => a + b, 0) / n
  const std = n > 1 ? Math.sqrt(values.reduce((a, b) => a + (b - mean) ** 2, 0) / (n - 1)) : null
  return [
    ['count', String(n)],
    ['mean', mean.toFixed(1)],
    ['std', std === null ? '—' : std.toFixed(2)],
    ['min', String(Math.min(...values))],
    ['max', String(Math.max(...values))],
  ]
}

=======
>>>>>>> Stashed changes
function Footnote({ text }) {
  if (!text) return null
  return <div className="rounded-lg border border-line bg-soft px-4 py-3.5 text-xs text-g1">{text}</div>
}

<<<<<<< Updated upstream
function NlqResultTabs({ def, tab, onTab, sortAsc, onToggleSort, sortKey, rows, footnote }) {
  const { type, x, y } = readViz(def)
  const stats = buildStats(def, y)
  const viz = def.visualization
  const columns = def.columns ?? []
  const groupBy = def.group_by ?? []
  // 바 폭은 항상 값/최대값 비율 (하드코딩 % 금지)
  const max = Math.max(0, ...rows.map((r) => Number(r[y]) || 0))
=======
const statsRows = (metricResult) => {
  if (metricResult == null) return null
  if (Array.isArray(metricResult))
    return metricResult.map((item, index) => [
      Object.entries(item.group ?? {})
        .map(([key, value]) => `${key}=${value}`)
        .join(' · ') || `group ${index + 1}`,
      String(item.value ?? '—'),
    ])
  if (typeof metricResult === 'object') return Object.entries(metricResult).map(([key, value]) => [key, String(value)])
  return [['result', String(metricResult)]]
}

function LinePlot({ rows, xKey, yKey }) {
  const plottedRows = rows
    .map((row, index) => ({ row, index, value: Number(row[yKey]) }))
    .filter((item) => Number.isFinite(item.value))
  const values = plottedRows.map((item) => item.value)
  if (!plottedRows.length) return null
  const width = 760
  const height = 250
  const left = 54
  const right = 24
  const top = 18
  const bottom = 42
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = Math.max(1, max - min)
  const points = plottedRows.map(({ row, index, value }, pointIndex) => ({
    label: String(row[xKey] ?? index + 1),
    value,
    x: left + (pointIndex * (width - left - right)) / Math.max(1, plottedRows.length - 1),
    y: top + ((max - value) * (height - top - bottom)) / span,
  }))

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="block w-full rounded-lg border border-line bg-white">
      <line x1={left} y1={top} x2={left} y2={height - bottom} className="stroke-line" />
      <line x1={left} y1={height - bottom} x2={width - right} y2={height - bottom} className="stroke-line" />
      <polyline
        points={points.map((point) => `${point.x},${point.y}`).join(' ')}
        fill="none"
        strokeWidth="2"
        className="stroke-blue"
      />
      {points.map((point, index) => (
        <g key={`${point.label}-${index}`}>
          <circle cx={point.x} cy={point.y} r="4" className="fill-white stroke-blue" strokeWidth="2" />
          <text x={point.x} y={height - 20} textAnchor="middle" className="fill-g1 text-[9px]">
            {point.label.length > 12 ? `${point.label.slice(0, 11)}…` : point.label}
          </text>
          <title>{`${point.label}: ${point.value}`}</title>
        </g>
      ))}
      <text x={left - 8} y={top + 4} textAnchor="end" className="fill-g1 text-[9px]">
        {max}
      </text>
      <text x={left - 8} y={height - bottom + 4} textAnchor="end" className="fill-g1 text-[9px]">
        {min}
      </text>
    </svg>
  )
}

function NlqResultTabs({ def, tab, onTab, sortAsc, onToggleSort, rows, footnote }) {
  const columns = def.columns ?? []
  const visualization = def.visualization ?? { chart_type: 'table', x: null, y: null }
  const xKey = columns.includes(visualization.x) ? visualization.x : columns[0]
  const yKey = columns.includes(visualization.y) ? visualization.y : columns.at(-1)
  const max = Math.max(0, ...rows.map((row) => Number(row[yKey]) || 0))
  const metricRows = statsRows(def.metric_result)
  const chartable = ['bar', 'line', 'histogram'].includes(visualization.chart_type) && xKey && yKey
>>>>>>> Stashed changes

  return (
    <Card className="animate-[om-fadein_.25s]">
      <CardHeader
        title="결과"
        note={
          <span className="font-mono">
            {def.row_count ?? rows.length}행 · {(def.latency_ms ?? 0).toLocaleString()}ms
          </span>
        }
      />

      <div className="flex items-center justify-between px-5 pb-4">
        <div className="flex gap-2">
          {TABS.map(([key, label]) => (
            <Button key={key} sm variant={tab === key ? 'primary' : 'outline'} onClick={() => onTab(key)}>
              {label}
            </Button>
          ))}
        </div>
<<<<<<< Updated upstream
        {viz && (
          <span className="font-mono text-[11px] text-g1">
            chart_type = {viz.chart_type} · x = {viz.x ?? '—'} · y = {viz.y ?? '—'}
          </span>
        )}
=======
        <span className="font-mono text-[11px] text-g1">
          chart_type = {visualization.chart_type} · x = {visualization.x ?? '—'} · y = {visualization.y ?? '—'}
        </span>
>>>>>>> Stashed changes
      </div>

      {tab === 'table' && (
        <div className="flex flex-col gap-3 px-5 pb-5">
          <table className="w-full border-collapse">
            <thead>
              <tr>
<<<<<<< Updated upstream
                {columns.map((c) => {
                  const sortable = columns.length > 1 && c === sortKey
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
                <tr key={`${String(r[x])}-${i}`} className={rowClass(i)}>
                  {columns.map((c, j) => (
                    <td key={c} className={`${TD_CLS} ${j === 0 && columns.length > 1 ? CELL_ID : CELL_MONO}`}>
                      {String(r[c])}
=======
                {columns.map((column) => (
                  <th
                    key={column}
                    onClick={column === yKey ? onToggleSort : undefined}
                    className={`${TH_CLS} font-mono ${column === yKey ? 'cursor-pointer' : ''}`}
                  >
                    {column}
                    {column === yKey && <span className="ml-1 text-[9px] text-g2">{sortAsc ? '▲' : '▼'}</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={`${def.nl_query_log_id}-${rowIndex}`} className={rowClass(rowIndex)}>
                  {columns.map((column, columnIndex) => (
                    <td key={column} className={`${TD_CLS} ${columnIndex === 0 && columns.length > 1 ? CELL_ID : CELL_MONO}`}>
                      {String(row[column] ?? '')}
>>>>>>> Stashed changes
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
<<<<<<< Updated upstream
          <div className="text-xs text-g1">
            metric = <span className="font-mono font-bold text-navy">{metricLabel(def.metric)}</span>
            {groupBy.length > 0 && (
              <>
                {' · '}group_by = <span className="font-mono font-bold text-navy">{groupBy.join(', ')}</span>
              </>
            )}
          </div>
          <KVGrid items={metricItems(def)} />
          {stats ? (
            <>
              <KVGrid items={stats} />
              <div className="text-xs text-g1">
                std는 표본 표준편차(ddof=1) 기준 · {y ?? '—'} 컬럼 · 결과 행에서 계산
              </div>
            </>
          ) : (
            <div className="rounded-lg border border-line bg-soft px-4 py-3.5 text-xs text-g1">
              수치형 집계 컬럼이 없어 요약 통계를 제공하지 않습니다 — metric_result 만 표기합니다
            </div>
          )}
=======
          {metricRows ? (
            <KVGrid items={metricRows} />
          ) : (
            <div className="rounded-lg border border-line bg-soft px-4 py-3.5 text-xs text-g1">
              그룹별 결과는 표 또는 차트 탭에서 확인해 주세요.
            </div>
          )}
          <div className="text-xs text-g1">
            metric = {def.metric?.type ?? '—'} · column = {def.metric?.column ?? '—'}
          </div>
>>>>>>> Stashed changes
          <Footnote text={footnote} />
        </div>
      )}

      {tab === 'chart' && (
        <div className="flex flex-col gap-[22px] px-6 pb-5 pt-1.5">
<<<<<<< Updated upstream
          {type === 'bar' ? (
            rows.map((r) => (
              <div key={String(r[x])}>
                <div className="mb-2 font-mono text-xs font-bold text-navy">{String(r[x])}</div>
                <div className="flex items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <HBar value={Number(r[y]) || 0} max={max} />
                  </div>
                  <span className="w-[26px] font-mono text-xs font-bold text-navy">{String(r[y])}</span>
=======
          {chartable && visualization.chart_type === 'line' ? (
            <LinePlot rows={rows} xKey={xKey} yKey={yKey} />
          ) : chartable ? (
            rows.map((row, rowIndex) => (
              <div key={`${String(row[xKey])}-${rowIndex}`}>
                <div className="mb-2 font-mono text-xs font-bold text-navy">{String(row[xKey])}</div>
                <div className="flex items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <HBar value={Number(row[yKey]) || 0} max={max} />
                  </div>
                  <span className="w-[52px] text-right font-mono text-xs font-bold text-navy">{String(row[yKey])}</span>
>>>>>>> Stashed changes
                </div>
              </div>
            ))
          ) : (
            <div className="rounded-lg border border-line bg-soft px-4 py-3.5 text-xs text-g1">
              응답의 visualization이 표 렌더링을 지정했습니다. 표 탭을 이용해 주세요.
            </div>
          )}
          <Footnote text={footnote} />
        </div>
      )}
    </Card>
  )
}

export default NlqResultTabs
