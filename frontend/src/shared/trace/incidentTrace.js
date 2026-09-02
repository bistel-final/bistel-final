export function incidentTraceScope(selected, alarms, { maxSensors = 4, maxWafers = 8 } = {}) {
  if (!selected?.lot_id || !selected?.chamber_id) return null
  const incident = [selected, ...(alarms ?? [])].filter(
    (item) => item?.lot_id === selected.lot_id && item?.chamber_id === selected.chamber_id,
  )
  const selectedSensor = selected.sensor_id ?? selected.parameter_id ?? null
  const uniqueSensors = [...new Set(
    incident.map((item) => item.sensor_id ?? item.parameter_id).filter(Boolean),
  )]
  const sensor_ids = [
    ...(selectedSensor ? [selectedSensor] : []),
    ...uniqueSensors.filter((sensor) => sensor !== selectedSensor).sort(),
  ].slice(0, maxSensors)
  const toWaferNo = (value) => value == null ? null : Number(value)
  const selectedWafer = toWaferNo(selected.wafer_no)
  const uniqueWafers = [...new Set(
    incident.map((item) => toWaferNo(item.wafer_no)).filter(Number.isFinite),
  )]
  const wafer_nos = [
    ...(Number.isFinite(selectedWafer) ? [selectedWafer] : []),
    ...uniqueWafers.filter((wafer) => wafer !== selectedWafer).sort((a, b) => a - b),
  ].slice(0, maxWafers)
  if (!sensor_ids.length || !wafer_nos.length) return null
  return {
    chamber_id: selected.chamber_id,
    sensor_ids,
    lot_id: selected.lot_id,
    wafer_nos,
  }
}

// 알람 화면은 선택 파라미터의 같은 LOT 전체를 한 번에 받아 wafer 선택기로 사용한다.
// chamber를 제한하지 않아 LOT의 25장을 모두 고를 수 있지만, 화면에는 한 번에 선택한
// wafer 하나만 그린다. wafer_nos 생략은 /traces/search 계약상 해당 scope 전체다.
export function alarmTrendScope(selected) {
  const sensorId = selected?.sensor_id ?? selected?.parameter_id ?? null
  if (!selected?.lot_id || !sensorId) return null
  return {
    sensor_ids: [sensorId],
    lot_id: selected.lot_id,
  }
}
