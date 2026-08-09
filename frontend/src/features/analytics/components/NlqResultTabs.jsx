// 결과 영역 — 표 / 통계 / 차트 3개 탭
// 차트 정보는 visualization.chart_type · x · y 를 우선 읽고, 없으면 기존 chart 필드로 폴백한다.
import { BarChart, Bar, XAxis, LabelList, ResponsiveContainer } from 'recharts'

const TABS = [
  ['table', '표'],
  ['stats', '통계'],
  ['chart', '차트'],
]

const STAT_LABELS = {
  count: '행 수 (count)',
  mean: '평균 (mean)',
  std: '표본 표준편차 (std, ddof=1)',
  min: '최솟값 (min)',
  max: '최댓값 (max)',
}

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

function NlqResultTabs({ def, tab, onTab, sortAsc, onToggleSort, rows, footnote }) {
  const { type, xIdx, yIdx } = readViz(def)
  const stats = buildStats(def, yIdx)
  const barData = rows.map((r) => ({ label: String(r[xIdx]), value: r[yIdx] }))

  return (
    <div className="animate-[om-fadein_.25s] overflow-hidden rounded-xl border border-line bg-white shadow-[0_1px_3px_rgba(15,42,92,.05)]">
      <div className="flex items-center border-b border-line bg-page px-[18px]">
        {TABS.map(([key, label]) => (
          <div
            key={key}
            onClick={() => onTab(key)}
            className="cursor-pointer border-b-[3px] px-[18px] py-3 text-sm font-extrabold"
            style={{
              color: tab === key ? '#1E5FC2' : '#64748B',
              borderColor: tab === key ? '#1E5FC2' : 'transparent',
            }}
          >
            {label}
          </div>
        ))}
        <span className="ml-auto font-mono text-[12.5px] font-bold text-slate">
          {def.rows ? `row_cnt ${def.rows.length} · ${def.lat}ms` : `row_cnt — · ${def.lat}ms`}
        </span>
      </div>

      {tab === 'table' && !def.rows && (
        <div className="p-8 text-center">
          <div className="text-[15px] font-extrabold text-navy">계측 FAIL 6건 — 상세 목록 실측 미제공</div>
          <div className="mt-1.5 text-[13.5px] font-semibold text-slate">
            계측 결과 행 데이터를 주시면 표를 채웁니다 (PASS율 85.0% · 34/40 기준)
          </div>
        </div>
      )}

      {tab === 'table' && def.rows && (
        <div className="flex flex-col gap-3 px-[18px] py-4">
          <div className="min-w-[420px] max-w-full self-start overflow-x-auto rounded-[10px] border border-line">
            <div className="flex border-b border-line bg-page">
              {def.cols.map((c, i) => {
                const sortable = !def.noSort && i === def.cols.length - 1
                return (
                  <div
                    key={c}
                    onClick={sortable ? onToggleSort : undefined}
                    className="flex flex-1 cursor-pointer items-center gap-1.5 px-4 py-2.5 font-mono text-[13px] font-extrabold text-navy hover:bg-line-soft"
                  >
                    {c}
                    {sortable && <span className="text-[10px] text-brand">{sortAsc ? '▲' : '▼'}</span>}
                  </div>
                )
              })}
            </div>
            {rows.map((r, i) => (
              <div key={i} className="flex border-b border-line-soft hover:bg-page">
                {r.map((v, j) => (
                  <div
                    key={j}
                    className="flex-1 px-4 py-[9px] font-mono text-sm text-ink"
                    style={{ fontWeight: j === r.length - 1 ? 800 : 600 }}
                  >
                    {String(v)}
                  </div>
                ))}
              </div>
            ))}
          </div>
          {footnote && (
            <div className="rounded-lg bg-page px-3.5 py-2.5 text-[12.5px] font-semibold text-slate">※ {footnote}</div>
          )}
        </div>
      )}

      {tab === 'stats' && (
        <div className="flex flex-col gap-3 px-[18px] py-4">
          {stats ? (
            <>
              <div className="flex flex-wrap gap-2.5">
                {stats.rows.map(([k, v]) => (
                  <div key={k} className="min-w-[132px] rounded-lg bg-page px-4 py-2.5 text-center">
                    <div className="font-mono text-[11px] font-bold text-slate-light">{STAT_LABELS[k] ?? k}</div>
                    <div className="mt-0.5 font-mono text-[17px] font-extrabold text-navy">{v}</div>
                  </div>
                ))}
              </div>
              <div className="text-[12.5px] font-semibold text-slate">
                std는 <b className="font-extrabold text-navy">표본 표준편차(ddof=1)</b> 기준입니다 ·{' '}
                {def.cols?.[yIdx] ?? '—'} 컬럼
                {stats.derived ? ' · 결과 행에서 계산' : ' · 실행 결과 제공값'}
              </div>
              {footnote && (
                <div className="rounded-lg bg-page px-3.5 py-2.5 text-[12.5px] font-semibold text-slate">
                  ※ {footnote}
                </div>
              )}
            </>
          ) : (
            <div className="rounded-[10px] border border-dashed border-line-input bg-page p-7 text-center text-sm font-bold text-slate">
              수치형 집계 컬럼이 없어 요약 통계를 제공하지 않습니다 — 표 탭을 이용하세요
            </div>
          )}
        </div>
      )}

      {tab === 'chart' && (
        <div className="px-[22px] py-[18px]">
          <div className="mb-3.5 flex flex-wrap items-center gap-2.5">
            <span className="rounded-md border border-[#BFDBFE] bg-[#F0F6FF] px-2.5 py-[3px] font-mono text-xs font-extrabold text-brand">
              {type === 'bar' ? '자동 선택: bar' : '표로 강등됨'}
            </span>
            {(def.corrected || type === 'demote') && (
              <span className="rounded-md bg-ooc-soft px-2.5 py-[3px] text-[12.5px] font-bold text-ooc">
                {def.corrected ? 'line → bar로 보정됨 (범주형 x축)' : '집계 형태가 차트와 호환되지 않아 표로 강등됨'}
              </span>
            )}
            {type === 'bar' && (
              <span className="font-mono text-[12px] font-bold text-slate-light">
                x: {def.cols?.[xIdx]} · y: {def.cols?.[yIdx]}
              </span>
            )}
          </div>
          {type === 'bar' && def.rows && (
            <div className="h-[264px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={barData} margin={{ top: 24, right: 20, left: 20, bottom: 0 }} barCategoryGap="35%">
                  <XAxis
                    dataKey="label"
                    axisLine={{ stroke: '#CBD5E1', strokeWidth: 2 }}
                    tickLine={false}
                    tick={{
                      fill: '#475569',
                      fontSize: 12.5,
                      fontWeight: 700,
                      fontFamily: 'ui-monospace, SF Mono, Menlo, monospace',
                    }}
                  />
                  <Bar dataKey="value" fill="#1E5FC2" radius={[6, 6, 0, 0]} isAnimationActive maxBarSize={88}>
                    <LabelList
                      dataKey="value"
                      position="top"
                      style={{
                        fill: '#0F2A5C',
                        fontSize: 15,
                        fontWeight: 800,
                        fontFamily: 'ui-monospace, SF Mono, Menlo, monospace',
                      }}
                    />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
          {type === 'demote' && (
            <div className="rounded-[10px] border border-dashed border-line-input bg-page p-7 text-center text-sm font-bold text-slate">
              차트로 표현할 수 없는 결과 형태 — 표 탭을 이용하세요
            </div>
          )}
          {type === 'bar' && footnote && (
            <div className="mt-3 rounded-lg bg-page px-3.5 py-2.5 text-[12.5px] font-semibold text-slate">
              ※ {footnote}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default NlqResultTabs
