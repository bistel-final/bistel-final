// 결과 컬럼 표시명 — 관리자가 읽는 한글 열 제목.
//
// 원칙: LLM 이 지은 별칭(chamber_count 등)은 번역하지 않는다. 그 열이 어떻게 계산됐는지(생성 SQL 의
// `<식> AS <별칭>`)를 읽어 우리가 제목을 짓는다. 식은 원본 컬럼(유한, 아래 사전) + 함수 몇 개로만
// 이뤄지므로 별칭이 무엇이든 제목이 나온다. 식을 읽을 수 없으면(CASE WHEN 등) 별칭 원본을 그대로 둔다.
//
// 정식 컬럼 사전(백엔드 시맨틱 레이어)이 생기면 이 파일은 프론트 폴백 층으로 남는다.

// ── 1층: 원본 컬럼 사전 — 허용 테이블의 컬럼 전부 (runtime manifest 기준 145개) ──
export const COLUMN_LABEL = {
  // 식별자
  alarm_id: '알람 ID', lot_id: '로트', lot: '로트', wafer_id: '웨이퍼', wafer: '웨이퍼', wafer_no: '웨이퍼 번호',
  chamber: '챔버', chamber_id: '챔버', equipment: '설비', equipment_id: '설비', area: '영역', area_id: '영역',
  parameter: '파라미터', parameter_id: '파라미터', parameter_name: '파라미터', recipe: '레시피', recipe_id: '레시피',
  device_id: '디바이스', model_code: '설비 모델', step_id: '공정 단계', step_no: '공정 순번', step_seq: '공정 순번',
  recipe_step_name: '레시피 스텝', recipe_step_no: '레시피 스텝 번호', lot_hist_id: '로트 이력 ID', lot_seq: '로트 순번',
  metrology_id: '계측 ID', doc_id: '문서 ID', agent_run_id: '실행 ID', action_id: '조치 ID', approval_id: '승인 ID',
  audit_id: '기록 번호', review_id: '리뷰 ID', tool_call_id: '툴 호출 ID', thread_id: '스레드 ID', retry_of_run_id: '재시도 원 실행',
  entity_id: '대상 ID', entity_type: '대상 종류', actor_id: '행위자 ID', actor_type: '행위자', provider_message_id: '전송 메시지 ID',
  request_hash: '요청 해시', trigger_alarm_id: '트리거 알람', trigger_alarm_source: '트리거 알람 종류', trigger_alarm_lot_hist_id: '트리거 로트 이력',
  trigger_wafer_no: '트리거 웨이퍼', representative_alarm_id: '대표 알람', representative_alarm_source: '대표 알람 종류',
  requested_alarm_id: '요청 알람', requested_alarm_source: '요청 알람 종류', member_alarm_refs: '포함 알람', member_wafer_refs: '포함 웨이퍼',
  // 알람·판정
  alarm_type: '알람 유형', alarm_source: '알람 종류', alarm_result: '판정', is_representative: '대표 여부', severity: '심각도',
  limit_type: '한계 종류', limit_value: '한계값', upper_only: '상한만', cl: '중심선', ucl: '관리상한', lcl: '관리하한',
  ctrl_upper: '관리상한', ctrl_lower: '관리하한', spec_upper: '규격상한', spec_lower: '규격하한', spec_center: '규격 중심', target_value: '목표값',
  // 측정값
  value: '측정값', value_mean: '평균값', value_max: '최대값', value_min: '최소값', value_std: '표준편차', stat_value: '통계값',
  statistic_type: '통계 종류', point_cnt: '측정 점수', measured_value: '계측값', measure_type: '계측 항목', unit: '단위',
  chamber_wafer_cum: '챔버 누적 웨이퍼', duration_sec: '소요 시간(초)', seq_no: '순번', input_tokens: '입력 토큰', output_tokens: '출력 토큰',
  latency_ms: '지연(ms)', attempt_count: '시도 횟수', call_seq: '호출 순번', confidence: '신뢰도', version: '버전',
  // 시각
  occurred_at: '발생 시각', measured_at: '계측 시각', track_in_at: '투입 시각', track_out_at: '반출 시각', created_at: '생성 시각',
  started_at: '시작 시각', ended_at: '종료 시각', completed_at: '완료 시각', requested_at: '요청 시각', decided_at: '결정 시각',
  approved_at: '승인 시각', reviewed_at: '리뷰 시각', called_at: '호출 시각', linked_at: '연결 시각', notify_at: '알림 시각', mes_at: 'MES 전송 시각',
  // 상태·조치
  status: '상태', action: '조치', action_code: '조치 코드', approval_status: '승인 상태', approval_required: '승인 필요', approved_by: '승인자',
  decided_by: '결정자', decision_comment: '결정 의견', disposition: '처분', channel: '채널', notify_status: '알림 상태', mes_status: 'MES 상태',
  autonomy_level: '자율 수준', policy_version: '정책 버전', result: '결과', reason: '사유', comment: '의견', detail: '상세', event_type: '이벤트 유형',
  before_json: '변경 전', after_json: '변경 후', last_error: '마지막 오류', error_msg: '오류 메시지', link_role: '연결 역할',
  // Fault·모델
  fault_code: 'Fault', predicted_fault_code: '예측 Fault', reviewed_fault_code: '리뷰 Fault', label_source: '라벨 출처', cause_summary: '원인 요약',
  evidence: '근거', reviewer: '리뷰어', llm_model: 'LLM 모델', prompt_version: '프롬프트 버전', tool_name: '툴 이름', input: '입력', output: '출력',
  // 문서
  title: '제목', doc_type: '문서 종류', source_path: '원본 경로',
}

// 사물형 컬럼 — COUNT 하면 "건수"가 아니라 "수"
const THING = new Set([
  'chamber', 'chamber_id', 'equipment', 'equipment_id', 'wafer', 'wafer_id', 'wafer_no', 'lot', 'lot_id',
  'parameter', 'parameter_id', 'parameter_name', 'recipe', 'recipe_id', 'area', 'area_id', 'device_id', 'step_id', 'model_code', 'doc_id',
])

const stripQualifier = (s) =>
  String(s)
    .trim()
    .replace(/^[a-z_][a-z0-9_]*\./i, '')
    .replace(/^"(.*)"$/, '$1')
const base = (col) => COLUMN_LABEL[stripQualifier(col).toLowerCase()] ?? null

// COUNT(*) 의 대상 — FROM 테이블로 정한다 (알람 테이블이면 "알람 건수")
const TABLE_SUBJECT = [
  [/alarm_history$/, '알람'],
  [/^lot_history$/, '로트 이력'],
  [/^metrology$/, '계측'],
  [/^action_history$/, '조치'],
  [/^agent_run$/, '실행'],
  [/^approval_request$/, '승인 요청'],
  [/^audit_log$/, '기록'],
  [/^document$/, '문서'],
]
function fromTable(sql) {
  const m = String(sql ?? '').replace(/\s+/g, ' ').match(/\sfrom\s+([a-z_][a-z0-9_]*)/i)
  return m ? m[1].toLowerCase() : null
}
function countAllLabel(sql) {
  const t = fromTable(sql)
  const hit = t && TABLE_SUBJECT.find(([re]) => re.test(t))
  return hit ? `${hit[1]} 건수` : '건수'
}

// ── 2층: 식 → 제목 ──
export function labelForExpr(expr, sql) {
  const e = String(expr ?? '').trim()
  if (!e) return null
  let m
  if (/^count\s*\(\s*(\*|1)\s*\)$/i.test(e)) return countAllLabel(sql)
  if ((m = e.match(/^count\s*\(\s*distinct\s+([\w."]+)\s*\)$/i))) {
    const b = base(m[1])
    return b ? `${b} 수` : null
  }
  if ((m = e.match(/^count\s*\(\s*([\w."]+)\s*\)$/i))) {
    const col = stripQualifier(m[1]).toLowerCase()
    const b = base(col)
    return b ? (THING.has(col) ? `${b} 수` : `${b} 건수`) : null
  }
  if ((m = e.match(/^(sum|avg|max|min|stddev|stddev_samp|stddev_pop)\s*\(\s*([\w."]+)\s*\)$/i))) {
    const b = base(m[2])
    if (!b) return null
    const fn = m[1].toLowerCase()
    if (fn === 'sum') return `${b} 합계`
    if (fn === 'avg') return `평균 ${b}`
    if (fn === 'max') return `최대 ${b}`
    if (fn === 'min') return `최소 ${b}`
    return `${b} 표준편차`
  }
  // 날짜 캐스트·절단: CAST(x AS DATE) · x::date · DATE(x) · DATE_TRUNC('day'|'week'|'month', x) (바깥 CAST 포함)
  if (/^(cast\s*\(.*\s+as\s+date\s*\)|.*::\s*date|date\s*\(.*\)|date_trunc\s*\(\s*'(day|week|month)'.*\))$/i.test(e)) {
    if (/'week'/i.test(e)) return '주'
    if (/'month'/i.test(e)) return '월'
    return '날짜'
  }
  if (/^date_trunc\s*\(\s*'hour'/i.test(e)) return '시간'
  if (/^[\w."]+$/.test(e)) return base(e)
  return null
}

// SELECT 목록을 최상위 콤마로 나눠 { alias(또는 컬럼명) → 식 } 을 만든다 — 문자열·괄호 안의 콤마는 무시
export function selectExprMap(sql) {
  const map = {}
  if (!sql) return map
  const m = String(sql)
    .replace(/\s+/g, ' ')
    .match(/^\s*select\s+(distinct\s+)?(.*?)\s+from\s/i)
  if (!m) return map
  const items = []
  let depth = 0
  let inStr = false
  let cur = ''
  for (const ch of m[2]) {
    if (ch === "'") inStr = !inStr
    if (!inStr) {
      if (ch === '(') depth++
      if (ch === ')') depth--
      if (ch === ',' && depth === 0) {
        items.push(cur.trim())
        cur = ''
        continue
      }
    }
    cur += ch
  }
  if (cur.trim()) items.push(cur.trim())
  for (const item of items) {
    const a = item.match(/^(.*?)\s+as\s+("?[\w]+"?)$/i)
    if (a) map[a[2].replace(/"/g, '')] = a[1].trim()
    else map[stripQualifier(item)] = item
  }
  return map
}

// ── 공개 API: 결과 컬럼 이름 → 표시명. 못 만들면 원본 그대로. ──
export function columnLabel(def, col) {
  if (!col) return col
  const expr = selectExprMap(def?.generated_sql)[col]
  return (expr && labelForExpr(expr, def?.generated_sql)) ?? base(col) ?? col
}
