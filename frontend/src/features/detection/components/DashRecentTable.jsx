import { Link } from 'react-router-dom'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import Badge from '../../../shared/components/ui/Badge.jsx'
import {
  rowClass,
  ruleVariant,
  judgementClass,
  TH_CLS,
  TD_CLS,
  CELL_ID,
  CELL_DIM,
} from '../../../shared/components/ui/statusStyles.js'
import { fmtShort } from '../../../shared/api/format.js'

const HEADERS = ['알람', '시각', '파라미터', '챔버', '룰', '타입', '조치']

// 최근 알람 테이블 — API 계약의 최신 5건. R03 행은 row-red + bg-red 배지, 짝수행 alt.
function DashRecentTable({ recents }) {
  return (
    <Card className="min-w-0 flex-1">
      <CardHeader title="최근 알람" note="시간순 · 누르면 파형" />
      <div className="px-3 pb-2.5">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              {HEADERS.map((h) => (
                <th key={h} className={TH_CLS}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {recents.length === 0 && (
              <tr>
                <td colSpan={HEADERS.length} className={`${TD_CLS} text-center text-g2`}>
                  선택한 조건에 해당하는 알람이 없습니다
                </td>
              </tr>
            )}
            {recents.map((a, i) => {
              const isR03 = a.rule_id.startsWith('R03')
              return (
                <tr key={a.alarm_id} className={rowClass(i, { red: isR03 })}>
                  <td className={TD_CLS}>
                    <Link to={`/alarms/${a.alarm_id}`} className={CELL_ID}>
                      {a.alarm_id}
                    </Link>
                  </td>
                  <td className={`${TD_CLS} ${CELL_DIM}`}>{fmtShort(a.occurred_at)}</td>
                  <td className={`${TD_CLS} font-mono font-semibold`}>{a.sensor_id}</td>
                  <td className={`${TD_CLS} ${CELL_DIM}`}>{a.chamber_id}</td>
                  <td className={TD_CLS}>
                    {/* 시안은 축약 표기(R01/R03) — 색은 statusStyles 공통 매핑 */}
                    <Badge variant={ruleVariant(a.rule_id)}>{a.rule_id.slice(0, 3)}</Badge>
                  </td>
                  <td className={`${TD_CLS} font-mono font-bold ${judgementClass(a.judgement)}`}>{a.judgement}</td>
                  <td className={TD_CLS}>
                    {a.action_id && a.latest_agent_run_id ? (
                      <Link to={`/agent-runs/${a.latest_agent_run_id}`} className="font-mono">
                        {a.action_id}
                      </Link>
                    ) : a.action_id ? (
                      <span className="font-mono">{a.action_id}</span>
                    ) : (
                      <span className="font-mono text-g2">—</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

export default DashRecentTable
