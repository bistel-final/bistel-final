// 생성 SQL 표시용 개행 — 의미는 바꾸지 않고 공백만 재배치한다.
// LLM 은 한 줄로 내놓는데, 사람이 읽으려면 절(clause) 단위로 줄이 끊겨야 한다.
// 문자열 리터럴 안의 키워드는 건드리지 않는다. 검증·실행은 이 결과를 그대로 passthrough 해도 안전하다(공백 차이).

const CLAUSES = ['FROM', 'WHERE', 'GROUP BY', 'HAVING', 'ORDER BY', 'LIMIT', 'OFFSET', 'UNION ALL', 'UNION']
const JOINS = ['LEFT JOIN', 'RIGHT JOIN', 'INNER JOIN', 'FULL JOIN', 'CROSS JOIN', 'JOIN']

// 리터럴('...')을 자리표시자로 빼두고 키워드를 처리한 뒤 되돌린다
const MARK = '\uE000' // private-use char: never appears in SQL, passes eslint no-control-regex
function protectLiterals(sql) {
  const literals = []
  const masked = sql.replace(/'(?:[^']|'')*'/g, (m) => {
    literals.push(m)
    return `${MARK}${literals.length - 1}${MARK}`
  })
  const re = new RegExp(`${MARK}(\\d+)${MARK}`, 'g')
  return { masked, restore: (s) => s.replace(re, (_, i) => literals[Number(i)]) }
}

export function formatSql(sql) {
  if (!sql || /\n/.test(sql.trim())) return sql // 이미 개행이 있으면 LLM·사용자 형식을 존중
  const { masked, restore } = protectLiterals(sql.replace(/\s+/g, ' ').trim())
  let out = masked
  for (const kw of [...JOINS, ...CLAUSES]) {
    const re = new RegExp(`\\s+${kw.replace(' ', '\\s+')}\\s+`, 'gi')
    out = out.replace(re, (m) => `\n${m.trim().toUpperCase()} `)
  }
  // WHERE 절 안의 AND/OR 는 들여쓴 새 줄로
  out = out.replace(/\s+(AND|OR)\s+/gi, (_, w) => `\n  ${w.toUpperCase()} `)
  // SELECT 목록이 길면 컬럼마다 줄 — 최상위 콤마만 (괄호 깊이 0)
  const lines = out.split('\n')
  if (lines[0] && lines[0].length > 60) {
    const head = lines[0].replace(/^SELECT\s+/i, '')
    const cols = []
    let depth = 0
    let cur = ''
    for (const ch of head) {
      if (ch === '(') depth++
      if (ch === ')') depth--
      if (ch === ',' && depth === 0) {
        cols.push(cur.trim())
        cur = ''
      } else cur += ch
    }
    if (cur.trim()) cols.push(cur.trim())
    if (cols.length > 1) lines[0] = `SELECT\n  ${cols.join(',\n  ')}`
  }
  return restore(lines.join('\n'))
}
