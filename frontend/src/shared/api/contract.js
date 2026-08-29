const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key)

export function assertExactObject(value, allowedKeys, label) {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`)
  }
  const unknown = Object.keys(value).filter((key) => !allowedKeys.includes(key))
  if (unknown.length) throw new TypeError(`${label} has unknown fields: ${unknown.join(', ')}`)
  return value
}

export function requireNonEmptyString(value, label) {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new TypeError(`${label} must be a non-empty string`)
  }
  return value.trim()
}

export function compactParams(value) {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined))
}

export function requireDatePair(value, label) {
  const hasFrom = own(value, 'date_from') && value.date_from !== undefined
  const hasTo = own(value, 'date_to') && value.date_to !== undefined
  if (hasFrom !== hasTo) throw new TypeError(`${label} requires date_from and date_to together`)
}

