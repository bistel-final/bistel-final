import { Card } from '../../../shared/components/ui/Card.jsx'

// 시안 v2 03_트레이스뷰어 차트 — SVG viewBox 1000×260 직접 렌더링 (recharts 제거)
//
// 시안과 의도된 차이:
// - x축은 measured_at 실제 시간축이다 (시안은 18포인트 등간격). 웨이퍼 경계·알람 시점 모두
//   실측 시각에서 x 를 계산한다. 웨이퍼 내부 포인트 간격은 균등 배치 렌더링 가정 —
//   TraceModel.buildSeries 주석 참고 (포인트별 measured_at 실측 미확보).
// - y 변환은 한계선 앵커 고정 선형: 최대 한계선→40.2 · 최소 한계선→191.1
//   (PH_FOCUS: 60→40.2, 0→115.6, −60→191.1 · y = 115.6 − v×1.2567).
//   데이터가 캔버스 헤드룸(y 20~212)을 벗어나는 조합에서만 그 방향 앵커를 데이터 극값으로
//   확장한다 — 시안 검수 조건(LOT-260007 W1·3·5, max 69.377→y 28.4)에서는 발동하지 않는다.

// fdc.css 토큰 (SVG 속성은 hex 직접 지정)
const C = {
  red: '#B03A4E',
  amber: '#B97F14',
  blue: '#2062A8',
  navy: '#1E3A5C',
  g1: '#5A6B7E',
  g2: '#98A5B3',
  chipBg: '#EBF2F9', // .t-blue
  chipLine: '#BBD2E8',
  dash: '#CBD4DE', // .dashed-card
}

const LIMIT_STROKE = { USL: C.red, LSL: C.red, UCL: C.amber, LCL: C.amber, TARGET: C.g2 }

// 시안 좌표계
const PX0 = 45 // 한계선 좌단
const PX1 = 945 // 한계선 우단
const PTX0 = 55 // 포인트 x 도메인
const PTX1 = 939
const CHIP_Y = 4
const CHIP_H = 18
const BOUND_Y1 = 18
const BOUND_Y2 = 215
const LABEL_Y = 235
const CAPTION_Y = 254
const Y_TOP = 40.2 // 최대 한계선 y
const Y_BOT = 191.1 // 최소 한계선 y
const Y_MIN = 20 // 데이터 허용 헤드룸
const Y_MAX = 212

// 단위 — 지시서·시안 제공값. 그 외 센서는 단위 실측 미제공
const SENSOR_UNIT = { PH_FOCUS: 'nm', PH_DOSE: 'mJ/cm²' }

// PH_DOSE 한계선 — 시안 v2 제공값. trace·한계선 모두 fixture 미제공인 센서의 골격에만 사용.
// TODO(data): fdc_trace.csv 확보 시 catalog.limits 실측으로 대체
const SKELETON_LIMITS = {
  PH_DOSE: [
    { label: 'USL', value: 26 },
    { label: 'UCL', value: 25.6 },
    { label: 'TARGET', value: 25 },
    { label: 'LCL', value: 24.4 },
    { label: 'LSL', value: 24 },
  ],
}

// value → y. 한계선 최대/최소를 40.2/191.1 에 앵커한 선형 변환 (위 주석 참고)
function scaleOf(limits, values) {
  const lv = limits.map((l) => l.value)
  let hiV = Math.max(...lv)
  let loV = Math.min(...lv)
  let hiY = Y_TOP
  let loY = Y_BOT
  if (hiV === loV) return () => (Y_TOP + Y_BOT) / 2
  if (values.length) {
    const k = (loY - hiY) / (hiV - loV)
    const dMax = Math.max(...values)
    const dMin = Math.min(...values)
    if (hiY - (dMax - hiV) * k < Y_MIN) {
      hiV = dMax
      hiY = Y_MIN
    }
    if (loY + (loV - dMin) * k > Y_MAX) {
      loV = dMin
      loY = Y_MAX
    }
  }
  const k = (loY - hiY) / (hiV - loV)
  return (v) => hiY + (hiV - v) * k
}

// epoch ms → "HH:MM" (Asia/Seoul 고정 — 실행 환경 타임존에 좌우되지 않게 직접 offset)
const KST_MS = 9 * 60 * 60 * 1000
const hhmm = (ms) => new Date(ms + KST_MS).toISOString().slice(11, 16)

// 상단 칩 — t-blue(웨이퍼) / solid blue(강조 웨이퍼) / bg-red(알람)
function Chip({ x, text, anchor = 'left', tone = 'tint' }) {
  const w = Math.round(text.length * 6.2) + 18
  const left = anchor === 'center' ? x - w / 2 : x
  const fill = tone === 'red' ? C.red : tone === 'solid' ? C.blue : C.chipBg
  return (
    <g>
      <rect x={left} y={CHIP_Y} rx={9} width={w} height={CHIP_H} fill={fill} stroke={tone === 'tint' ? C.chipLine : 'none'} />
      <text
        x={left + w / 2}
        y={16.5}
        fontSize={9.5}
        fontWeight={tone === 'tint' ? 600 : 700}
        fill={tone === 'tint' ? C.blue : '#fff'}
        textAnchor="middle"
      >
        {text}
      </text>
    </g>
  )
}

function TraceChart({ sensor, chamber, lot, series, markers = [], r03Keys = new Set(), limits, lim, focus }) {
  const { segments, points, missing } = series
  const noTrace = segments.length === 0 // 이 센서의 trace 행 자체가 없다 (예: PH_DOSE)
  const chartLimits = noTrace ? (SKELETON_LIMITS[sensor] ?? limits) : limits
  const yOf = scaleOf(chartLimits, points.map((p) => p.value))

  const t0 = segments[0]?.start ?? 0
  const t1 = segments.length ? segments[segments.length - 1].end : 1
  const xOf = (ms) => PTX0 + ((ms - t0) / Math.max(1, t1 - t0)) * (PTX1 - PTX0)

  const unit = SENSOR_UNIT[sensor]
  const poly = points.map((p) => `${xOf(p.x).toFixed(1)},${yOf(p.value).toFixed(1)}`).join(' ')

  return (
    <Card className="px-5 pb-2.5 pt-4">
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="font-mono text-[14px] font-extrabold text-navy">{sensor}</span>
        <span className="font-mono text-[11.5px] text-g1">
          {chamber} · {unit ? `단위 ${unit}` : '단위 실측 미제공'}
        </span>
      </div>

      <svg viewBox="0 0 1000 260" className="block w-full" fontFamily="IBM Plex Mono, monospace">
        {/* 한계선 5개 + 좌 값 · 우 이름 라벨 */}
        {chartLimits.map((l) => {
          const y = yOf(l.value)
          const stroke = LIMIT_STROKE[l.label] ?? C.g2
          const dashed = l.label !== 'TARGET'
          return (
            <g key={l.label}>
              <line
                x1={PX0}
                y1={y}
                x2={PX1}
                y2={y}
                stroke={stroke}
                strokeWidth={1}
                strokeDasharray={dashed ? '4 4' : undefined}
                opacity={dashed ? 0.7 : 1}
              />
              <text x={38} y={y + 3} fontSize={9} fill={C.g1} textAnchor="end">
                {l.value}
              </text>
              <text x={950} y={y + 3} fontSize={8.5} fill={stroke}>
                {l.label}
              </text>
            </g>
          )
        })}

        {/* 웨이퍼 경계 점선 + 상단 칩 (경계 x = occurred_at 실측) */}
        {segments.map((s, i) => (
          <g key={s.key}>
            {i > 0 && (
              <line
                x1={xOf(s.start)}
                y1={BOUND_Y1}
                x2={xOf(s.start)}
                y2={BOUND_Y2}
                stroke={C.g2}
                strokeWidth={1}
                strokeDasharray="4 4"
                opacity={0.7}
              />
            )}
            <Chip
              x={i === 0 ? PTX0 : xOf(s.start) + 46}
              text={i === 0 ? `${lot} · W${s.waferNo}` : `W${s.waferNo}`}
              tone={focus.has(s.waferNo) ? 'solid' : 'tint'}
            />
          </g>
        ))}

        {/* 알람 시점 — 적색 세로 점선 + bg-red 칩 (occurred_at 시간값에서 x 계산) */}
        {markers.map((m) => (
          <g key={m.ms}>
            <line x1={xOf(m.ms)} y1={BOUND_Y1} x2={xOf(m.ms)} y2={BOUND_Y2} stroke={C.red} strokeWidth={1.2} strokeDasharray="3 3" />
            <Chip x={xOf(m.ms)} anchor="center" text={m.ids.join(' · ')} tone="red" />
          </g>
        ))}

        {/* 파형 — 폴리라인 파랑 1.8 + 포인트(흰 채움 파랑 스트로크, R03 대상 USL 초과점은 적색 채움) */}
        {points.length > 0 && <polyline points={poly} fill="none" stroke={C.blue} strokeWidth={1.8} />}
        {points.map((p) => {
          const cx = xOf(p.x)
          const cy = yOf(p.value)
          const alarmPt = r03Keys.has(`${p.waferNo}|${p.step}`) && p.value > lim.USL
          return (
            <g key={`${p.key}-${p.step}-${p.seq}`}>
              {focus.has(p.waferNo) && <circle cx={cx} cy={cy} r={6.6} fill="none" stroke={C.blue} strokeWidth={1.2} opacity={0.5} />}
              <circle cx={cx} cy={cy} r={3.6} fill={alarmPt ? C.red : '#fff'} stroke={C.blue} strokeWidth={1.6}>
                <title>{`W${p.waferNo} · ${p.step} · P${p.seq}/${p.of} · ${p.value.toFixed(3)}`}</title>
              </circle>
            </g>
          )
        })}

        {/* 실측 미제공 안내 — 값을 만들지 않는다 */}
        {points.length === 0 && (
          <g>
            <rect x={270} y={84} width={460} height={88} rx={10} fill="#fff" stroke={C.dash} strokeWidth={1.5} strokeDasharray="6 5" />
            <text x={500} y={122} fontSize={13} fontWeight={700} fill={C.navy} textAnchor="middle">
              {noTrace ? 'trace 실측 미제공' : '포인트 실측 미제공 — 전 스텝 points: null'}
            </text>
            <text x={500} y={146} fontSize={10.5} fill={C.g1} textAnchor="middle">
              fdc_trace.csv 확보 대기 · TODO(data)
            </text>
          </g>
        )}

        {/* 하단 x라벨 — 조회 구간 시작/중간/끝 시각 */}
        {segments.length > 0 && (
          <g fontSize={9.5} fill={C.g1}>
            <text x={PTX0} y={LABEL_Y}>
              {hhmm(t0)}
            </text>
            <text x={(PTX0 + PTX1) / 2} y={LABEL_Y} textAnchor="middle">
              {hhmm((t0 + t1) / 2)}
            </text>
            <text x={PTX1} y={LABEL_Y} textAnchor="end">
              {hhmm(t1)}
            </text>
            <text x={(PTX0 + PTX1) / 2} y={CAPTION_Y} textAnchor="middle" fill={C.g2}>
              시각
            </text>
          </g>
        )}
      </svg>

      {missing.length > 0 && points.length > 0 && (
        <div className="mt-1 font-mono text-[10.5px] text-g2">
          포인트 실측 미제공: {missing.map((m) => `W${m.waferNo}/${m.step}`).join(', ')} (points: null)
        </div>
      )}
      {segments.length > 0 && (
        <div className="mt-0.5 pb-1 text-[10.5px] text-g2">
          웨이퍼 경계는 occurred_at 실측 · 웨이퍼 내부 포인트 간격은 균등 배치 렌더링 가정 (포인트별 measured_at 실측 미확보)
        </div>
      )}
    </Card>
  )
}

export default TraceChart
