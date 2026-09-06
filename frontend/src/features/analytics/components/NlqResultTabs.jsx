// 결과 — 표 / 차트 (+ 교차확인 시 그래프) 탭. 통계 탭은 관리자에게 의미 없는 값이라 제거(#309).
// 명세 AnalysisQueryResponse: rows 는 객체 배열이라 컬럼 접근은 row[columns[i]] 다.
// 차트는 visualization.chart_type · x · y 를 그대로 읽는다.
import Button from '../../../shared/components/ui/Button.jsx'
import { Card } from '../../../shared/components/ui/Card.jsx'
import { BarChart, LineChart } from './NlqCharts.jsx'
import { inferUnit } from './nlqUnits.js'
import { columnLabel } from './columnLabels.js'
import NlqGraphTab from './NlqGraphTab.jsx'
import { CELL_ID, CELL_MONO, TD_CLS, TH_CLS, rowClass } from '../../../shared/components/ui/statusStyles.js'

const TABS = [
  ['table', '표'],
  ['chart', '차트'],
]
// 교차확인이 실제로 돌았을 때(MATCH/MISMATCH)만 그래프 탭이 생긴다
const GRAPH_TAB = ['graph', '그래프']
const hasGraphTab = (def) => ['MATCH', 'MISMATCH'].includes(def?.cross_check?.status)

// visualization.chart_type/x/y — 컬럼 목록에 없는 이름은 첫/마지막 컬럼으로 폴백한다
// 차트 종류는 응답이 확정한다 — UI 는 재판단 없이 그리기만 한다 (FR-D-04)
// drawable charts: bar and line only. Histogram is demoted to the table because pre-binned SQL output and raw values cannot be told apart (#309).
const DRAWABLE = ['bar', 'line']
function readViz(def) {
  const columns = def.columns ?? []
  const viz = def.visualization ?? {}
  const type = DRAWABLE.includes(viz.chart_type) ? viz.chart_type : 'demote'
  const x = columns.includes(viz.x) ? viz.x : columns[0]
  const y = columns.includes(viz.y) ? viz.y : columns[columns.length - 1]
  return { type, x, y }
}

function Footnote({ text }) {
  if (!text) return null
  return <div className="rounded-lg border border-line bg-soft px-4 py-3.5 text-xs text-g1">{text}</div>
}

function NlqResultTabs({ def, tab, onTab, sortDir, onToggleSort, sortKey, rows, footnote }) {
  const { type, x, y } = readViz(def)
  const columns = def.columns ?? []
  // 단위는 응답의 y 컬럼명이 근거다 (창작 금지) — raw 히스토그램은 값 컬럼 빈도
  const unit = inferUnit(def, y) // COUNT 계열만 건/장/개, 모르면 null

  return (
    <Card className="animate-[om-fadein_.25s]">
      {/* 결과 헤더 — 발표 스케일: 교차확인 결과를 배지+한 줄 설명으로 크게, 행 수는 navy mono */}
      <div className="flex items-center justify-between gap-4 px-6 pb-3 pt-5">
        <div className="flex items-center gap-3">
          <span className="text-[15px] font-bold tracking-[-.01em] text-ink">결과</span>
          <span className="font-mono text-[13px] text-g1">
            <span className="font-bold text-navy">{def.row_count ?? rows.length}</span>행
          </span>
        </div>
      </div>

      <div className="flex items-center justify-between px-6 pb-4">
        <div className="flex gap-2">
          {[...TABS, ...(hasGraphTab(def) ? [GRAPH_TAB] : [])].map(([key, label]) => (
            <Button key={key} sm variant={tab === key ? 'primary' : 'outline'} onClick={() => onTab(key)}>
              {label}
            </Button>
          ))}
        </div>
      </div>

      {tab === 'table' && (
        <div className="flex flex-col gap-3 px-5 pb-5">
          {rows.length === 0 && (
            <div className="rounded-lg border border-line bg-soft px-4 py-3.5 text-xs text-g1">
              조건에 맞는 데이터가 없습니다 (0행) — 질의는 정상 실행되었습니다.
              필터 값이 실제 데이터에 존재하는지(예: 값이 다른 테이블 소속일 수 있음) 확인해 보세요.
            </div>
          )}
          <table className="w-full border-collapse">
            <thead>
              <tr>
                {columns.map((c) => {
                  const sortable = columns.length > 1 && c === sortKey
                  const label = columnLabel(def, c)
                  // "챔버 수" 처럼 제목이 이미 세는 것을 말하므로 (개)·(건) 단위는 중복이라 버린다
                  const showUnit = c === y && unit && !/(수|건수)$/.test(label)
                  return (
                    <th
                      key={c}
                      onClick={sortable ? onToggleSort : undefined}
                      title={`${c}${sortable ? (sortDir ? ' · 클릭: 다음 정렬 (마지막은 원래 순서)' : ' · 클릭: 큰 값부터 정렬') : ''}`}
                      className={`${TH_CLS} ${sortable ? 'cursor-pointer select-none' : ''}`}
                    >
                      {label}
                      {showUnit && <span className="ml-1 font-sans font-medium text-g2">({unit})</span>}
                      {sortable && sortDir && <span className="ml-1 text-[9px] text-g2">{sortDir === 'asc' ? '▲' : '▼'}</span>}
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

      {tab === 'chart' && (
        <div className="flex flex-col gap-[22px] px-6 pb-5 pt-1.5">
          {type === 'bar' ? (
            <BarChart rows={rows} x={x} y={y} unit={unit} />
          ) : type === 'line' ? (
            <LineChart rows={rows} x={x} y={y} unit={unit} />
          ) : (
            <div className="rounded-lg border border-line bg-soft px-4 py-3.5 text-xs text-g1">
              이 결과는 표로 보는 것이 정확합니다. 표 탭을 이용해 주세요.
            </div>
          )}
          <Footnote text={footnote} />
        </div>
      )}
      {tab === 'graph' && hasGraphTab(def) && (
        <div className="px-6 pb-5 pt-1.5">
          <NlqGraphTab def={def} />
        </div>
      )}
    </Card>
  )
}

export default NlqResultTabs
