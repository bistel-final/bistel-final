// 결과 — 표 / 통계 / 차트 3개 탭 (디자인 v2 06)
// 명세 AnalysisQueryResponse: rows 는 객체 배열이라 컬럼 접근은 row[columns[i]] 다.
// 차트는 visualization.chart_type · x · y 를 그대로 읽는다.
import Button from '../../../shared/components/ui/Button.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import KVGrid from '../../../shared/components/ui/KVGrid.jsx'
import { BarChart, HistogramChart, LineChart } from './NlqCharts.jsx'
import { CELL_ID, CELL_MONO, TD_CLS, TH_CLS, rowClass } from '../../../shared/components/ui/statusStyles.js'

const TABS = [
  ['table', '표'],
  ['stats', '통계'],
  ['chart', '차트'],
]

// visualization.chart_type/x/y — 컬럼 목록에 없는 이름은 첫/마지막 컬럼으로 폴백한다
// 차트 종류는 응답이 확정한다 — UI 는 재판단 없이 그리기만 한다 (FR-D-04)
const DRAWABLE = ['bar', 'line', 'histogram']
function readViz(def) {
  const columns = def.columns ?? []
  const viz = def.visualization ?? {}
  const type = DRAWABLE.includes(viz.chart_type) ? viz.chart_type : 'demote'
  const x = columns.includes(viz.x) ? viz.x : columns[0]
  const y = columns.includes(viz.y) ? viz.y : columns[columns.length - 1]
  return { type, x, y, rawY: viz.y ?? null }
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

function Footnote({ text }) {
  if (!text) return null
  return <div className="rounded-lg border border-line bg-soft px-4 py-3.5 text-xs text-g1">{text}</div>
}

function NlqResultTabs({ def, tab, onTab, sortAsc, onToggleSort, sortKey, rows, footnote }) {
  const { type, x, y, rawY } = readViz(def)
  const stats = buildStats(def, y)
  const viz = def.visualization
  const columns = def.columns ?? []
  const groupBy = def.group_by ?? []
  // 단위는 응답의 y 컬럼명이 근거다 (창작 금지) — raw 히스토그램은 값 컬럼 빈도
  const unitLabel = viz?.y ?? (type === 'histogram' && viz?.x ? `${viz.x} 빈도` : null)

  return (
    <Card className="animate-[om-fadein_.25s]">
      <CardHeader
        title="결과"
        note={<span className="font-mono">{def.row_count ?? rows.length}행</span>}
      />

      <div className="flex items-center justify-between px-5 pb-4">
        <div className="flex gap-2">
          {TABS.map(([key, label]) => (
            <Button key={key} sm variant={tab === key ? 'primary' : 'outline'} onClick={() => onTab(key)}>
              {label}
            </Button>
          ))}
        </div>
        {unitLabel && (
          <span className="font-mono text-[11px] text-g1">단위 · {unitLabel}</span>
        )}
      </div>

      {tab === 'table' && (
        <div className="flex flex-col gap-3 px-5 pb-5">
          <table className="w-full border-collapse">
            <thead>
              <tr>
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
          <Footnote text={footnote} />
        </div>
      )}

      {tab === 'chart' && (
        <div className="flex flex-col gap-[22px] px-6 pb-5 pt-1.5">
          {type === 'bar' ? (
            <BarChart rows={rows} x={x} y={y} />
          ) : type === 'line' ? (
            <LineChart rows={rows} x={x} y={y} />
          ) : type === 'histogram' ? (
            <HistogramChart rows={rows} x={x} y={rawY} />
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
