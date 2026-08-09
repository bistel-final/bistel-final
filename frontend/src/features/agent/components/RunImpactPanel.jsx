import RunEvidenceCard from './RunEvidenceCard.jsx'
import StatusBadge from '../../../shared/components/StatusBadge.jsx'

// 설비 관계 실측 — 다른 feature의 mock을 import하지 않고 이 화면 상수로 둔다
const FLOW = {
  edge: 'UPSTREAM_OF',
  nodes: [
    { id: 'PHO-01', model: 'PH-9000', role: '상류' },
    { id: 'ETC-01', model: 'ET-7500', role: '하류' },
  ],
}

// 계측 CD FAIL 실측 6건. TODO(data): CD 규격 한계값은 미제공이라 표에 넣지 않는다.
const METROLOGY_FAILS = [
  { id: 'MET-0016', lot: 'LOT-260004', wafer: '6', param: 'CD_AEI', value: 41.82, result: 'FAIL' },
  { id: 'MET-0020', lot: 'LOT-260005', wafer: '6', param: 'CD_AEI', value: 41.41, result: 'FAIL' },
  { id: 'MET-0029', lot: 'LOT-260008', wafer: '1', param: 'CD_ADI', value: 41.59, result: 'FAIL' },
  { id: 'MET-0031', lot: 'LOT-260008', wafer: '1', param: 'CD_AEI', value: 41.2, result: 'FAIL' },
  { id: 'MET-0033', lot: 'LOT-260009', wafer: '1', param: 'CD_ADI', value: 41.4, result: 'FAIL' },
  { id: 'MET-0035', lot: 'LOT-260009', wafer: '1', param: 'CD_AEI', value: 39.59, result: 'FAIL' },
]

// 값은 실측 그대로 보여준다 (41.2 → "41.20" 처럼 자릿수만 맞춤)
const fmtCd = (v) => v.toFixed(2)

function Cond({ hit, label, note }) {
  return (
    <div
      className="flex items-start gap-2 rounded-[10px] border px-3 py-2"
      style={
        hit
          ? { borderColor: '#FECACA', background: '#FEE2E2' }
          : { borderColor: '#E2E8F0', background: '#FFFFFF' }
      }
    >
      <StatusBadge tone={hit ? 'oos' : 'muted'} mono>
        {hit ? '해당' : '미해당'}
      </StatusBadge>
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="text-[12.5px] font-extrabold" style={{ color: hit ? '#DC2626' : '#475569' }}>
          {label}
        </span>
        <span className="font-mono text-[11.5px] font-semibold text-slate-light">{note}</span>
      </span>
    </div>
  )
}

function RunImpactPanel({ run, runAlarms, allAlarms, action, equipmentId }) {
  const inc = run.incident
  const { lot_id: lot, chamber_id: chamber, sensor_id: sensor } = inc
  const upstream = FLOW.nodes[0]
  const downstream = FLOW.nodes[1]
  const here = FLOW.nodes.find((n) => n.id === equipmentId)

  // 상향 조건 — CD_AEI 직접 연결 / 반복 LOT / 하류 전파
  const cdHits = METROLOGY_FAILS.filter((m) => m.lot === lot && m.param === 'CD_AEI')
  const repeatLots = [
    ...new Set(allAlarms.filter((a) => a.chamber_id === chamber && a.sensor_id === sensor).map((a) => a.lot_id)),
  ]
  const downstreamAlarms = allAlarms.filter((a) => a.lot_id === lot && a.equipment_id === downstream.id)

  // 하향 조건 — 순수 연쇄 이상(조치 미생성) / 단발 후 정상 복귀(MONITOR)
  const consec = runAlarms.filter((a) => a.rule_id === 'R03_CONSEC')
  const unitAlarms = runAlarms.filter((a) => a.rule_id !== 'R03_CONSEC')
  const oosAlarms = runAlarms.filter((a) => a.rule_id === 'R01_OOS')

  const up = [
    {
      label: 'CD_AEI 직접 연결',
      hit: cdHits.length > 0,
      note: cdHits.length ? `${lot} CD_AEI FAIL ${cdHits.length}건` : `${lot} CD_AEI 계측 FAIL 없음`,
    },
    {
      label: '반복 LOT',
      hit: repeatLots.length >= 2,
      note: `${chamber} · ${sensor} 알람 LOT ${repeatLots.length}개`,
    },
    {
      label: '하류 전파',
      hit: downstreamAlarms.length > 0,
      note: downstreamAlarms.length
        ? `${lot} 의 ${downstream.id} 알람 ${downstreamAlarms.length}건`
        : `${lot} 의 ${downstream.id} 알람 없음`,
    },
  ]
  const down = [
    {
      label: '순수 연쇄 이상 → 조치 미생성',
      hit: runAlarms.length > 0 && unitAlarms.length === 0,
      note: `연쇄 알람 ${consec.length}건 · 단위 알람 ${unitAlarms.length}건`,
    },
    {
      label: '단발 후 정상 복귀 → MONITOR',
      hit: oosAlarms.length === 1 && consec.length === 0,
      note: `OOS 알람 ${oosAlarms.length}건`,
    },
  ]
  const upHit = up.filter((c) => c.hit)
  const downHit = down.filter((c) => c.hit)

  const lotCdRows = METROLOGY_FAILS.filter((m) => m.lot === lot)

  return (
    <>
      <RunEvidenceCard
        index={4}
        title="상류 vs 하류"
        meta={`${upstream.id} ${FLOW.edge} ${downstream.id}`}
        reading={`상류 ${upstream.id} 의 이상이 하류 ${downstream.id} 까지 번졌는지 본다 — 상향 조건에 걸리면 조치 수위를 올리고, 하향 조건만 맞으면 낮춘다.`}
      >
        <div className="flex items-stretch gap-2.5">
          {FLOW.nodes.map((n, i) => (
            <div key={n.id} className="flex flex-1 items-center gap-2.5">
              <div
                className="flex flex-1 flex-col gap-0.5 rounded-[10px] border px-3.5 py-2.5"
                style={
                  n.id === equipmentId
                    ? { borderColor: '#1E5FC2', background: '#EDF2FA' }
                    : { borderColor: '#E2E8F0', background: '#FFFFFF' }
                }
              >
                <div className="flex items-center gap-1.5">
                  <span className="font-mono text-[13px] font-extrabold text-navy">{n.id}</span>
                  <span className="font-mono text-[11px] font-bold text-slate-light">{n.model}</span>
                  {n.id === equipmentId && (
                    <StatusBadge tone="blue" className="ml-auto">
                      incident
                    </StatusBadge>
                  )}
                </div>
                <div className="text-[11.5px] font-bold text-slate">{n.role} 공정</div>
              </div>
              {i === 0 && (
                <div className="flex flex-none flex-col items-center">
                  <span className="font-mono text-[10px] font-extrabold text-brand">{FLOW.edge}</span>
                  <span className="text-[16px] font-extrabold leading-none text-brand">→</span>
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="mt-3 grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1.5">
            <div className="text-[11.5px] font-extrabold text-slate-light">상향 조건</div>
            {up.map((c) => (
              <Cond key={c.label} {...c} />
            ))}
          </div>
          <div className="flex flex-col gap-1.5">
            <div className="text-[11.5px] font-extrabold text-slate-light">하향 조건</div>
            {down.map((c) => (
              <Cond key={c.label} {...c} />
            ))}
          </div>
        </div>

        <div className="mt-2.5 rounded-[10px] border border-line-soft bg-page px-3 py-2 text-[12.5px] font-bold text-ink">
          {here ? `incident 설비 ${here.id} 는 ${here.role}` : 'incident 설비 관계 실측 미제공'} · 상향 {upHit.length}건 ·
          하향 {downHit.length}건
          {action && (
            <>
              {' '}
              → 판정{' '}
              <span className="font-mono text-navy">
                {action.action_code} ({action.severity})
              </span>
            </>
          )}
        </div>
      </RunEvidenceCard>

      <RunEvidenceCard
        index={5}
        title="계측 CD 결과"
        meta={`FAIL ${METROLOGY_FAILS.length}건`}
        reading="CD 계측 FAIL은 센서 이탈이 실제 치수 불량까지 갔는지 보여준다 — incident LOT과 겹치면 상향 근거가 된다."
      >
        <div className="overflow-hidden rounded-[10px] border border-line">
          <div className="grid grid-cols-[96px_112px_64px_84px_1fr_64px] gap-2 border-b border-line bg-page px-3 py-2 text-[11px] font-extrabold text-slate">
            <span>계측 ID</span>
            <span>LOT</span>
            <span>WAFER</span>
            <span>항목</span>
            <span>값</span>
            <span>판정</span>
          </div>
          {METROLOGY_FAILS.map((m) => {
            const mine = m.lot === lot
            return (
              <div
                key={m.id}
                className="grid grid-cols-[96px_112px_64px_84px_1fr_64px] items-center gap-2 border-b border-line-soft px-3 py-2 last:border-b-0"
                style={mine ? { background: '#EDF2FA', boxShadow: 'inset 3px 0 0 #1E5FC2' } : undefined}
              >
                <span className="font-mono text-[12px] font-extrabold text-navy">{m.id}</span>
                <span className="font-mono text-[12px] font-bold text-ink">{m.lot}</span>
                <span className="font-mono text-[12px] font-bold text-slate">W{m.wafer}</span>
                <span className="font-mono text-[12px] font-bold text-ink">{m.param}</span>
                <span className="font-mono text-[12.5px] font-extrabold text-ink">{fmtCd(m.value)}</span>
                <span>
                  <StatusBadge tone="oos" mono>
                    {m.result}
                  </StatusBadge>
                </span>
              </div>
            )
          })}
        </div>
        <div className="mt-2 text-[11.5px] font-semibold text-slate-light">
          {lotCdRows.length > 0
            ? `현재 incident ${lot} 의 계측 FAIL ${lotCdRows.length}건 (표에서 강조)`
            : `현재 incident ${lot} 의 계측 FAIL은 목록에 없습니다.`}{' '}
          · CD 규격 한계값은 실측 미제공
        </div>
      </RunEvidenceCard>
    </>
  )
}

export default RunImpactPanel
