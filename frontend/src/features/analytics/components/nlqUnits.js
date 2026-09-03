// 결과 단위 추론 — 확실히 아는 것만 말하고 모르면 null (창작 금지).
// 근거는 생성 SQL 의 y 컬럼 식과 컬럼 이름이다. 측정값(AVG(value) 등)은 파라미터마다 단위가 달라 표시하지 않는다.

const COUNT_ALIAS = /(count|cnt|_n$|^n_|num)/i

function countUnitFor(text) {
  const t = String(text).toLowerCase()
  if (/wafer/.test(t)) return '장'
  if (/chamber|equipment|eqp|recipe|lot/.test(t)) return '개'
  return '건'
}

// y 컬럼이 SQL 에서 어떤 식으로 계산됐는지 찾는다: "<expr> AS <y>"
function exprOf(sql, y) {
  if (!sql || !y) return null
  const re = new RegExp(`([A-Za-z_]+\\s*\\([^)]*\\))\\s+AS\\s+${y}\\b`, 'i')
  return sql.match(re)?.[1] ?? null
}

export function inferUnit(def, y) {
  if (!def || !y) return null
  const expr = exprOf(def.generated_sql, y)
  if (expr && /^count\s*\(/i.test(expr)) {
    // COUNT(DISTINCT wafer) → 장, COUNT(*) → 컬럼 이름으로 대상 판단
    const inner = expr.replace(/^count\s*\(/i, '').replace(/\)$/, '')
    return countUnitFor(/^\s*\*?\s*$/.test(inner) ? y : inner)
  }
  if (!expr && COUNT_ALIAS.test(y)) return countUnitFor(y)
  return null
}
