const valuesOf = (items, key) => [...new Set((items ?? []).map((item) => item[key]).filter(Boolean))].sort()
const pick = (options, requested) => (options.includes(requested) ? requested : (options[0] ?? ''))

export function normalizeTraceFilters(catalog, raw = {}) {
  const areas = valuesOf(catalog?.areas, 'area_id')
  const area = pick(areas, raw.area)
  const areaEquipments = (catalog?.equipments ?? []).filter((item) => item.area_id === area)
  const equipments = valuesOf(areaEquipments, 'equipment_id')
  const equipment = pick(equipments, raw.equipment)
  const equipmentNode = areaEquipments.find((item) => item.equipment_id === equipment)
  const chambers = [...(equipmentNode?.chambers ?? [])].sort()
  const chamber = pick(chambers, raw.chamber)
  const sensorOptions = valuesOf(catalog?.sensors, 'sensor_id')
  const askedSensors = (raw.sensors ?? []).filter((sensor) => sensorOptions.includes(sensor))
  const sensors = askedSensors.length ? askedSensors : sensorOptions.slice(0, 1)
  const recipeOptions = valuesOf(catalog?.recipes, 'recipe_id')
  const recipe = raw.recipe && recipeOptions.includes(raw.recipe) ? raw.recipe : ''
  const lots = valuesOf(catalog?.lots, 'lot_id')
  const lot = lots.includes(raw.lot) ? raw.lot : (lots.at(-1) ?? '')
  const lotNode = (catalog?.lots ?? []).find((item) => item.lot_id === lot)
  const waferOptions = (lotNode?.wafer_nos ?? []).map(String).sort((a, b) => Number(a) - Number(b))
  const askedWafers = (raw.wafers ?? []).map(String).filter((wafer) => waferOptions.includes(wafer))
  const wafers = askedWafers.length ? askedWafers : waferOptions

  return {
    area,
    equipment,
    chamber,
    sensors,
    recipe,
    lot,
    wafers,
    from: raw.from ?? '',
    to: raw.to ?? '',
    options: { areas, equipments, chambers, sensors: sensorOptions, recipes: recipeOptions, lots, wafers: waferOptions },
  }
}
