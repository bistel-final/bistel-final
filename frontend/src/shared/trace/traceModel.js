// Detection 화면과 Agent 상세가 함께 쓰는 실측 trace 파생 로직.
// 응답에 없는 값은 만들지 않으며, 센서별 한계선만 사용한다.

export function limitLines(lim) {
  return [
    ['USL', lim?.spec_upper],
    ['UCL', lim?.ctrl_upper],
    ['TARGET', lim?.target],
    ['LCL', lim?.ctrl_lower],
    ['LSL', lim?.spec_lower],
  ]
    .filter(([, value]) => value != null)
    .map(([label, value]) => ({ label, value }))
}

export function judgeValue(value, lim) {
  if (value == null || !lim) return null
  const hasLimit = [lim.spec_lower, lim.ctrl_lower, lim.ctrl_upper, lim.spec_upper].some(
    (candidate) => candidate != null,
  )
  if (!hasLimit) return null
  if (lim.spec_upper != null && value > lim.spec_upper) return 'OOS'
  if (lim.spec_lower != null && value < lim.spec_lower) return 'OOS'
  if (lim.ctrl_upper != null && value > lim.ctrl_upper) return 'OOC'
  if (lim.ctrl_lower != null && value < lim.ctrl_lower) return 'OOC'
  return 'OK'
}

export function detailNumbers(detail) {
  const grab = (key) => {
    const match = String(detail ?? '').match(new RegExp(`${key}\\s+(-?[0-9.]+)`))
    return match ? Number(match[1]) : null
  }
  return { mean: grab('mean'), min: grab('min'), max: grab('max') }
}
