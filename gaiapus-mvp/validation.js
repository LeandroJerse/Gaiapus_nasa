// Adapted from the Claude Code review to the actual MVP product contract.
const UTC_DAY = 864e5;
const finite = Number.isFinite;

function utcStamp(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return NaN;
  const stamp = Date.parse(value + "T00:00:00Z");
  return finite(stamp) && new Date(stamp).toISOString().slice(0, 10) === value ? stamp : NaN;
}

export function validatedPackages(entries, onReject = () => {}) {
  const idCounts = new Map();
  for (const entry of entries) idCounts.set(entry?.id, (idCounts.get(entry?.id) ?? 0) + 1);
  return entries.filter((entry) => {
    const { id, uf, municipalityCode, cityGeometry, data } = entry ?? {};
    const prefix = typeof id === "string" && /^[a-z0-9-]+$/.test(id) ? id : null;
    const point = (xy) => Array.isArray(xy) && xy.length === 2 && xy.every(finite);
    const valid = (() => {
      if (!prefix || idCounts.get(id) !== 1 || !/^\d{2}$/.test(uf) ||
          !/^\d{7}$/.test(municipalityCode) || !municipalityCode.startsWith(uf)) return false;
      if (data?.validation?.status !== "validated" || !data?.field?.geojson ||
          !cityGeometry?.features?.some((feature) => String(feature.properties?.codarea) === municipalityCode)) return false;
      if (data.image?.url !== `./data/regions/${id}/scene.png` && !(id === "uberlandia" && data.image?.url === "./data/scene.png")) return false;
      if (data.image?.corners?.length !== 4 || !data.image.corners.every(point)) return false;
      if (!Array.isArray(data.rainfall) || !data.rainfall.length || !Array.isArray(data.observations)) return false;
      const first = utcStamp(data.rainfall[0]?.date);
      if (!finite(first) || !data.rainfall.every((row, i) =>
        utcStamp(row?.date) === first + i * UTC_DAY && finite(row.rain_mm) && row.rain_mm >= 0)) return false;
      const audit = data.validation.rain_crosscheck;
      const reference = data.reference_rainfall;
      if (audit?.product !== "IMERG daily Final V07B" || audit.matched_days !== data.rainfall.length ||
          !Array.isArray(reference) || reference.length !== data.rainfall.length ||
          !reference.every((row, i) => row?.date === data.rainfall[i].date && finite(row.rain_mm) && row.rain_mm >= 0)) return false;
      const last = first + (data.rainfall.length - 1) * UTC_DAY;
      const eligible = new Set();
      const ids = new Set();
      let imageMatches = false;
      for (const row of data.observations) {
        const day = utcStamp(row?.date);
        if (!finite(day) || day < first || day > last ||
            typeof row.scene_id !== "string" || !row.scene_id || ids.has(row.scene_id)) return false;
        ids.add(row.scene_id);
        if (row.ndvi === null) continue;
        if (!finite(row.ndvi) || Math.abs(row.ndvi) > 1 || !finite(row.valid_fraction) ||
            row.valid_fraction < 0.65 || row.valid_fraction > 1) return false;
        eligible.add(day);
        if (row.scene_id === data.image.scene_id && row.date === data.image.date) imageMatches = true;
      }
      return imageMatches && eligible.size >= 3;
    })();
    if (!valid) onReject(id);
    return valid;
  });
}
