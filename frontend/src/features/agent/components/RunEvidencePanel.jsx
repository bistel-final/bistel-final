import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import RunEvidenceCard from './RunEvidenceCard.jsx'
import RunTraceChart from './RunTraceChart.jsx'

// 도메인 감시 파라미터 8종 — 공정(센서 prefix)별 4종 (도메인 규칙, 임의 목록 아님)
const PARAMS_BY_PREFIX = {
  PH: ['PH_FOCUS', 'PH_DOSE', 'PH_PEB', 'PH_DEV'],
  ET: ['ET_REFL', 'ET_CF4', 'ET_PRES', 'ET_ESC'],
}

// 설비 관계 실측 — 다른 feature의 mock을 import하지 않고 이 화면 상수로 둔다
const FLOW = { upstream: 'PHO-01', upstreamArea: 'PHOTO', downstream: 'ETC-01', downstreamArea: 'ETCH' }

// 계측 CD FAIL 실측 6건 — LOT-260007 의 FAIL 은 실측에 없다 (읽는 법에서 미제공 처리)
const METROLOGY_FAILS = [
  { id: 'MET-0016', lot: 'LOT-260004', wafer: '6', param: 'CD_AEI' },
  { id: 'MET-0020', lot: 'LOT-260005', wafer: '6', param: 'CD_AEI' },
  { id: 'MET-0029', lot: 'LOT-260008', wafer: '1', param: 'CD_ADI' },
  { id: 'MET-0031', lot: 'LOT-260008', wafer: '1', param: 'CD_AEI' },
  { id: 'MET-0033', lot: 'LOT-260009', wafer: '1', param: 'CD_ADI' },
  { id: 'MET-0035', lot: 'LOT-260009', wafer: '1', param: 'CD_AEI' },
]

// ②~⑤ 비교 카드는 실측 trace가 없어 시안 04 의 개념 폴리라인 좌표를 그대로 쓴다 (개념 시각화)
const CONCEPT_PTS = {
  same: '10,50 110,42 210,48 310,40 410,46 510,42',
  sibling: '10,55 110,48 210,38 310,30 410,44 510,34',
  flow: '10,60 110,44 210,28 310,24 410,42 510,36',
  cd: '10,58 110,42 210,26 310,20 410,38 510,30',
}

// 알람 detail 문자열에서 실측 max 를 원문 그대로 뽑는다 (반올림·창작 금지)
const parseMax = (detail) => {
  const m = /max\s+([\d.]+)/.exec(detail ?? '')
  return m ? m[1] : null
}

// 실측 미제공 자리 — 차트를 그리지 않고 빈 자리임을 명시한다
function NoTrace({ label }) {
  return (
    <div className="flex min-h-[64px] w-full items-center justify-center font-mono text-[11px] text-g1">
      {label} — 실측 미제공
    </div>
  )
}

function RunEvidencePanel({ run, runAlarms, allAlarms, trace, equipmentId }) {
  // incident 는 (lot_id, chamber_id) 두 개뿐 — sensor_id·recipe_step_name 은 run 의 형제 필드
  const { lot_id: lot, chamber_id: chamber } = run.incident ?? {}
  const sensor = run.sensor_id
  const step = run.recipe_step_name
  // 한계선은 센서별로 다르다 — POST /traces/search 응답의 limits[sensor_id] 에서 읽는다
  const usl = trace?.limits?.[sensor]?.spec_upper ?? null

  // ── ① 문제 파라미터 — trace 응답(웨이퍼 오름차순 × seq_no 순서)에서 좌표 변환 ──
  const values = (trace?.wafers ?? [])
    .filter((w) => w.lot_id === lot && w.chamber_id === chamber && w.sensor_id === sensor)
    .sort((a, b) => Number(a.wafer_no) - Number(b.wafer_no))
    .flatMap((w) =>
      [...(w.points ?? [])]
        .sort((a, b) => a.seq_no - b.seq_no)
        .filter((p) => p.recipe_step_name === step)
        .map((p) => p.value),
    )

  // 읽는 법은 알람 실측에서 유도 — R03_CONSEC hit_cnt + 연쇄 창 안 OOS detail max 의 최대값
  const consec = runAlarms.find((a) => a.rule_id === 'R03_CONSEC') ?? null
  const oosAlarms = runAlarms.filter((a) => a.rule_id === 'R01_OOS')
  const consecWindow = consec
    ? oosAlarms.filter((a) => a.occurred_at <= consec.occurred_at).slice(-Number(consec.hit_cnt))
    : oosAlarms
  const maxes = consecWindow.map((a) => parseMax(a.detail)).filter(Boolean)
  const peak = maxes.length ? maxes.reduce((m, s) => (Number(s) > Number(m) ? s : m), maxes[0]) : null
  const oocCnt = runAlarms.filter((a) => a.judgement === 'OOC').length
  // 한계선 미제공 센서(ET_REFL)는 USL 을 창작하지 않고 빼고 쓴다
  const uslText = typeof usl === 'number' ? ` (USL ${usl})` : ''
  const read1 =
    consec && peak
      ? `연속 ${consec.hit_cnt} WAFER OOS · 최대 ${peak}${uslText}`
      : peak
        ? `OOS ${oosAlarms.length} WAFER · 최대 ${peak}${uslText}`
        : `OOS 없음 · OOC ${oocCnt}건 — 한계선 이탈 실측 미제공`

  // ── ② 같은 챔버 다른 파라미터 — 같은 챔버 나머지 감시 파라미터의 알람 유무 ──
  const others = (PARAMS_BY_PREFIX[String(sensor ?? '').slice(0, 2)] ?? []).filter((s) => s !== sensor)
  const otherAlarmCnt = allAlarms.filter((a) => a.chamber_id === chamber && others.includes(a.sensor_id)).length
  const read2 =
    others.length === 0
      ? '감시 파라미터 목록 실측 미제공'
      : otherAlarmCnt === 0
        ? `나머지 ${others.length} 종은 전 구간 IN_CONTROL — 이 파라미터만의 문제`
        : `나머지 ${others.length} 종에서 알람 ${otherAlarmCnt}건 — 챔버 공통 원인 의심`

  // ── ③ 형제 챔버 정상 WAFER — C1↔C2 를 비교군으로 삼는다 ──
  const chamberId = String(chamber ?? '')
  const sibling = /-C1$/.test(chamberId)
    ? chamberId.replace(/-C1$/, '-C2')
    : /-C2$/.test(chamberId)
      ? chamberId.replace(/-C2$/, '-C1')
      : null
  const oosCnt = runAlarms.filter((a) => a.judgement === 'OOS').length
  // OOS = 규격(USL/LSL) 밖 — 정상 밴드 이탈은 OOS 존재로만 판단하고, 없으면 창작하지 않는다
  const read3 = oosCnt > 0 ? '평소 밴드를 크게 벗어남' : `OOS 알람 없음 — 밴드 이탈 폭 실측 미제공`

  // ── ④ 상류 PHOTO vs 하류 ETCH — incident 반대편 설비의 같은 LOT 알람 유무 ──
  const isUpstream = equipmentId === FLOW.upstream
  const counterpart = isUpstream ? FLOW.downstream : FLOW.upstream
  const counterLabel = isUpstream ? '하류' : '상류'
  const propagated = allAlarms.filter((a) => a.lot_id === lot && a.equipment_id === counterpart).length
  const read4 = !equipmentId
    ? '설비 관계 실측 미제공'
    : propagated === 0
      ? `${counterLabel} ${counterpart} 은 전 구간 정상 — 독립 이상. 하향 대상 아님`
      : `${counterLabel} ${counterpart} 알람 ${propagated}건 — 연쇄 의심`
  const tag4 = equipmentId && propagated === 0 ? '하향 판정 근거' : null

  // ── ⑤ 계측 CD 결과 — incident LOT(가능하면 같은 WAFER)의 CD FAIL 실측만 인정 ──
  const waferSet = new Set(runAlarms.map((a) => String(a.wafer_no)))
  const lotFails = METROLOGY_FAILS.filter((m) => m.lot === lot)
  const waferFails = lotFails.filter((m) => waferSet.has(String(m.wafer)))
  const params5 = [...new Set((waferFails.length ? waferFails : lotFails).map((m) => m.param))].join(' · ')
  const read5 = waferFails.length
    ? `같은 WAFER ${params5} FAIL — 품질 영향 확인됨`
    : lotFails.length
      ? `같은 LOT ${params5} FAIL ${lotFails.length}건 — 품질 영향 확인됨`
      : `incident LOT ${lot} 의 CD 계측 FAIL 실측 미제공`
  const tag5 = lotFails.length ? '상향 판정 근거' : null

  return (
    <Card className="min-w-0 flex-1">
      <CardHeader title="근거" note="판단의 이유를 차트로" />
      <div className="flex flex-col gap-3.5 px-5 pb-5">
        <RunEvidenceCard color="red" title="문제 파라미터" sub={`${sensor} · ${step} — 한계선 대비 이탈 구간`} read={read1}>
          {values.length > 1 ? (
            <RunTraceChart color="red" values={values} limit={usl} />
          ) : (
            <NoTrace label={`${sensor} @ ${chamber} trace`} />
          )}
        </RunEvidenceCard>

        <RunEvidenceCard
          color="blue"
          title="같은 챔버 다른 파라미터"
          sub={others.length ? `${others.join(' · ')} 를 겹쳐 본다` : '비교 파라미터 실측 미제공'}
          read={read2}
        >
          <RunTraceChart color="blue" pts={CONCEPT_PTS.same} />
        </RunEvidenceCard>

        <RunEvidenceCard
          color="green"
          title={sibling ? `형제 챔버 ${sibling} 정상 WAFER` : '형제 챔버 정상 WAFER'}
          sub="형제 챔버 정상 WAFER 와 겹쳐 본다"
          read={sibling ? read3 : '형제 챔버 실측 미제공'}
        >
          <RunTraceChart color="green" pts={CONCEPT_PTS.sibling} />
        </RunEvidenceCard>

        <RunEvidenceCard
          color="navy"
          title={`상류 ${FLOW.upstreamArea} vs 하류 ${FLOW.downstreamArea}`}
          sub="연쇄인가 독립인가"
          read={read4}
          tag={tag4}
          tagVariant="t-navy"
        >
          <RunTraceChart color="navy" pts={CONCEPT_PTS.flow} />
        </RunEvidenceCard>

        <RunEvidenceCard
          color="amber"
          title="계측 CD 결과"
          sub="CD_ADI · CD_AEI 판정과 나란히"
          read={read5}
          tag={tag5}
          tagVariant="t-amber"
        >
          <RunTraceChart color="amber" pts={CONCEPT_PTS.cd} />
        </RunEvidenceCard>
      </div>
    </Card>
  )
}

export default RunEvidencePanel
