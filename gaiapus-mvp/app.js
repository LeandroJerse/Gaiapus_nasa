(function () {
// Adapted from the Claude Code review to the actual MVP product contract.
const UTC_DAY = 864e5;
const finite = Number.isFinite;

function utcStamp(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return NaN;
  const stamp = Date.parse(value + "T00:00:00Z");
  return finite(stamp) && new Date(stamp).toISOString().slice(0, 10) === value ? stamp : NaN;
}

function validatedPackages(entries, onReject = () => {}) {
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

const STYLE = "./data/globe-style.json";
const HOME = { center: [-50, -15], zoom: 1.4 };
const DAY = 86400000;
function featureBounds(feature) {
  const result = [[1 / 0, 1 / 0], [-1 / 0, -1 / 0]];
  function extend(coordinates) {
    if (typeof coordinates[0] !== "number") {
      coordinates.forEach(extend);
      return;
    }
    for (let axis = 0;axis < 2; axis += 1) {
      result[0][axis] = Math.min(result[0][axis], coordinates[axis]);
      result[1][axis] = Math.max(result[1][axis], coordinates[axis]);
    }
  }
  extend(feature.geometry.coordinates);
  return result;
}
const ts = (d) => Date.parse(d + "T00:00:00Z");
const br = (d) => d.slice(8, 10) + "/" + d.slice(5, 7);
const num = (v, n) => v == null ? "sem dado" : v.toFixed(n).replace(".", ",");
const reduced = () => !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
function Chart({ observations, rainfall }) {
  const dates = observations.map((o) => o.date).concat(rainfall.map((r) => r.date));
  if (!dates.length)
    return React.createElement("p", {
      className: "chart-empty"
    }, "Sem dados para o gráfico.");
  const W = 720, L = 52, PW = 652, AT = 34, AH = 130, BT = 214, BH = 110, H = 364;
  const all = dates.map(ts);
  const t0 = Math.min(...all) - DAY, span = Math.max(...all) + DAY - t0;
  const x = (d) => L + (ts(d) - t0) / span * PW;
  const pts = observations.filter((o) => o.ndvi != null);
  const lo = Math.min(0, ...pts.map((o) => o.ndvi));
  const yN = (v) => AT + AH - (v - lo) / (1 - lo) * AH;
  const rmax = Math.max(1, ...rainfall.map((r) => r.rain_mm || 0));
  const yR = (v) => BT + BH - v / rmax * BH;
  const bw = Math.max(1, PW / (span / DAY) * 0.8);
  const ticks = [0, 1, 2, 3, 4].map((i) => new Date(t0 + DAY + (span - 2 * DAY) * i / 4).toISOString().slice(0, 10));
  const grid = (y, label, key) => React.createElement("g", {
    key,
    className: "chart-tick"
  }, React.createElement("line", {
    x1: L,
    x2: L + PW,
    y1: y,
    y2: y,
    stroke: "currentColor",
    opacity: "0.15"
  }), React.createElement("text", {
    x: L - 6,
    y: y + 4,
    textAnchor: "end",
    fontSize: "11",
    fill: "currentColor"
  }, label));
  return React.createElement("svg", {
    className: "chart-svg",
    viewBox: `0 0 ${W} ${H}`,
    width: "100%",
    role: "img",
    "aria-labelledby": "chart-title chart-desc",
    preserveAspectRatio: "xMidYMid meet"
  }, React.createElement("title", {
    id: "chart-title"
  }, "NDVI por cena e chuva diária"), React.createElement("desc", {
    id: "chart-desc"
  }, "Dois painéis com o mesmo eixo de datas. Os valores estão na tabela abaixo."), React.createElement("g", {
    className: "chart-panel chart-panel-ndvi"
  }, React.createElement("text", {
    x: L,
    y: AT - 12,
    fontSize: "13",
    fontWeight: "600",
    fill: "currentColor"
  }, "NDVI por cena (HLS, recorte)"), [0, 0.5, 1].map((v) => grid(yN(v), num(v, 1), v)), pts.map((o) => React.createElement("circle", {
    key: o.scene_id,
    className: "chart-point",
    cx: x(o.date),
    cy: yN(o.ndvi),
    r: "4.5",
    fill: "#86efac"
  }, React.createElement("title", null, `${br(o.date)}: NDVI ${num(o.ndvi, 2)}`)))), React.createElement("g", {
    className: "chart-panel chart-panel-rain"
  }, React.createElement("text", {
    x: L,
    y: BT - 12,
    fontSize: "13",
    fontWeight: "600",
    fill: "currentColor"
  }, "Chuva diária em mm (IMERG Final V07B)"), [0, rmax / 2, rmax].map((v) => grid(yR(v), num(v, 0), v)), rainfall.filter((r) => r.rain_mm != null).map((r) => React.createElement("rect", {
    key: r.date,
    className: "chart-bar",
    x: x(r.date) - bw / 2,
    y: yR(r.rain_mm),
    width: bw,
    height: BT + BH - yR(r.rain_mm),
    fill: "#7dd3fc"
  }, React.createElement("title", null, `${br(r.date)}: ${num(r.rain_mm, 1)} mm`)))), React.createElement("g", {
    className: "chart-axis-x"
  }, ticks.map((d) => React.createElement("g", {
    key: d
  }, React.createElement("line", {
    x1: x(d),
    x2: x(d),
    y1: AT,
    y2: BT + BH,
    stroke: "currentColor",
    opacity: "0.1"
  }), React.createElement("text", {
    x: x(d),
    y: BT + BH + 16,
    textAnchor: "middle",
    fontSize: "11",
    fill: "currentColor"
  }, br(d)))), React.createElement("text", {
    x: L + PW / 2,
    y: H - 6,
    textAnchor: "middle",
    fontSize: "11",
    fill: "currentColor"
  }, "Data (dia/mês)")));
}
function App({ packages }) {
  const box = React.useRef(null);
  const mapRef = React.useRef(null);
  const admin = React.useRef(null);
  const current = React.useRef({});
  const [selection, select] = React.useState({});
  const [ready, setReady] = React.useState(false);
  const [err, setErr] = React.useState("");
  const { country, uf, city, field } = selection;
  const active = packages.find((entry) => entry.id === field && entry.uf === uf && entry.municipalityCode === city);
  const data = active?.data;
  const eligible = packages.filter((entry) => !uf || entry.uf === uf);
  const matching = city ? eligible.filter((entry) => entry.municipalityCode === city) : eligible;
  const level = field ? "field" : city ? "city" : uf ? "state" : country ? "country" : "globe";
  const go = (next) => {
    current.current = next;
    select(next);
  };
  const open = (entry) => go({ country: "BRA", uf: entry.uf, city: entry.municipalityCode, field: entry.id });
  const countries = country === "BRA" ? "Brasil" : selection.countryName || country;
  const ufName = admin.current?.stateNames.get(uf) || packages.find((entry) => entry.uf === uf)?.ufName || uf;
  const cityName = admin.current?.cityNames.get(city) || packages.find((entry) => entry.municipalityCode === city)?.label || city;
  const steps = [["Globo", {}]];
  if (country)
    steps.push([countries, { country, countryName: selection.countryName, center: selection.center }]);
  if (uf)
    steps.push([ufName, { country, uf }]);
  if (city)
    steps.push([cityName, { country, uf, city }]);
  if (field)
    steps.push(["Recorte demonstrativo", selection]);
  React.useEffect(() => {
    const gl = window.maplibregl;
    if (!gl || !box.current) {
      setErr("Globo indisponível. Os recortes podem ser abertos pelo seletor.");
      return;
    }
    let dead = false;
    let map;
    try {
      map = new gl.Map({
        container: box.current,
        style: STYLE,
        ...HOME,
        attributionControl: { compact: false, customAttribution: "Estilo: MapLibre demotiles" }
      });
    } catch {
      setErr("Globo 3D indisponível. Os recortes podem ser abertos pelo seletor.");
      return;
    }
    mapRef.current = map;
    map.addControl(new gl.NavigationControl, "top-right");
    map.on("error", (event) => console.warn("map", event.error));
    map.on("load", async () => {
      const read = async (name) => {
        const response = await fetch("./data/" + name);
        if (!response.ok)
          throw new Error(name);
        return response.json();
      };
      try {
        const [brazil, states, minasCities, stateNames, cityNames] = await Promise.all([
          read("BR.geojson"),
          read("states.geojson"),
          read("mg-cities.geojson"),
          read("state-names.json"),
          read("mg-city-names.json")
        ]);
        if (dead)
          return;
        const cities = new Map(minasCities.features.map((feature) => [String(feature.properties.codarea), feature]));
        const names = new Map(cityNames.map(({ id, nome }) => [String(id), nome]));
        for (const entry of packages) {
          const feature = entry.cityGeometry.features.find((item) => String(item.properties.codarea) === entry.municipalityCode);
          cities.set(entry.municipalityCode, feature);
          names.set(entry.municipalityCode, entry.label);
        }
        const color = "#5eead4";
        const cityFeatures = { type: "FeatureCollection", features: [...cities].map(([code, feature]) => ({
          ...feature,
          properties: { ...feature.properties, code, uf: code.slice(0, 2), available: packages.some((p) => p.municipalityCode === code) }
        })) };
        admin.current = {
          brazil: brazil.features[0],
          states: new Map(states.features.map((f) => [String(f.properties.codarea), f])),
          cities,
          stateNames: new Map(stateNames.map(({ id, nome }) => [String(id), nome])),
          cityNames: names
        };
        for (const [id, collection] of [["country-br", brazil], ["states", states], ["cities", cityFeatures]]) {
          map.addSource(id, { type: "geojson", data: collection });
          map.addLayer({
            id: id + "-fill",
            type: "fill",
            source: id,
            layout: { visibility: "none" },
            paint: {
              "fill-color": ["case", ["==", ["get", "available"], true], "#fcd34d", color],
              "fill-opacity": 0.2,
              "fill-outline-color": color
            }
          });
        }
        for (const entry of packages) {
          const { id, data } = entry;
          map.addSource("scene-" + id, { type: "image", url: data.image.url, coordinates: data.image.corners });
          map.addLayer({
            id: "scene-" + id,
            type: "raster",
            source: "scene-" + id,
            layout: { visibility: "none" },
            paint: { "raster-fade-duration": 0 }
          });
        }
        map.addSource("fields", { type: "geojson", data: { type: "FeatureCollection", features: packages.map((entry) => ({
          type: "Feature",
          properties: { id: entry.id, city: entry.municipalityCode },
          geometry: entry.data.field.geojson
        })) } });
        map.addLayer({
          id: "fields-fill",
          type: "fill",
          source: "fields",
          layout: { visibility: "none" },
          paint: { "fill-color": "#f472b6", "fill-opacity": 0.2, "fill-outline-color": "#f472b6" }
        });
        map.addLayer({
          id: "fields-line",
          type: "line",
          source: "fields",
          layout: { visibility: "none" },
          paint: { "line-color": "#f472b6", "line-width": 2 }
        });
        const pick = (point, layer) => map.getLayer(layer) ? map.queryRenderedFeatures(point, { layers: [layer] })[0] : undefined;
        map.on("click", (event) => {
          const state = current.current;
          if (!state.country) {
            if (pick(event.point, "country-br-fill")) {
              go({ country: "BRA" });
              return;
            }
            const props = pick(event.point, "countries-fill")?.properties;
            if (props?.ADM0_A3 === "BRA")
              go({ country: "BRA" });
            else if (props?.ADM0_A3)
              go({
                country: props.ADM0_A3,
                countryName: props.NAME || props.ADMIN,
                center: event.lngLat.toArray()
              });
            return;
          }
          if (state.country !== "BRA" || state.field)
            return;
          if (!state.uf) {
            const code = pick(event.point, "states-fill")?.properties?.codarea;
            if (code !== undefined)
              go({ country: "BRA", uf: String(code) });
          } else if (!state.city) {
            const code = pick(event.point, "cities-fill")?.properties?.code;
            if (code)
              go({ country: "BRA", uf: state.uf, city: String(code) });
          } else {
            const id = pick(event.point, "fields-fill")?.properties?.id;
            const entry = packages.find((item) => item.id === id && item.municipalityCode === state.city);
            if (entry)
              open(entry);
          }
        });
        setReady(true);
      } catch {
        if (!dead)
          setErr("Limites administrativos indisponíveis no mapa. Use o seletor para abrir os recortes.");
      }
    });
    return () => {
      dead = true;
      map.remove();
      mapRef.current = null;
    };
  }, [packages]);
  React.useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map)
      return;
    const duration = reduced() ? 0 : 1150;
    const visibility = (id, flag) => map.setLayoutProperty(id, "visibility", flag ? "visible" : "none");
    visibility("country-br-fill", !country);
    visibility("states-fill", country === "BRA" && !uf);
    visibility("cities-fill", !!uf && !city);
    map.setFilter("cities-fill", ["==", ["get", "uf"], uf || ""]);
    visibility("fields-fill", !!city && !active);
    visibility("fields-line", !!city);
    for (const id of ["fields-fill", "fields-line"])
      map.setFilter(id, active ? ["==", ["get", "id"], active.id] : ["==", ["get", "city"], city || ""]);
    for (const entry of packages)
      visibility("scene-" + entry.id, active?.id === entry.id);
    const boundary = city ? admin.current.cities.get(city) : uf ? admin.current.states.get(uf) : admin.current.brazil;
    const bounds = active?.data.bounds.field || boundary && featureBounds(boundary);
    if (country === "BRA" && bounds?.flat().every(Number.isFinite))
      map.fitBounds(bounds, { padding: 36, maxZoom: 15, duration });
    else
      map.easeTo(selection.center ? { center: selection.center, zoom: 3, duration } : { ...HOME, duration });
  }, [ready, selection, active, packages]);
  const status = active ? "A linha marca o recorte onde o NDVI foi calculado." : country && country !== "BRA" ? "Exploração global. A análise da competição está disponível somente em recortes publicados no Brasil." : uf && !matching.length ? "Ainda não há pacote validado nesta seleção." : city ? "Clique no recorte marcado ou abra pelo painel." : uf ? "Selecione um município com dados validados." : country ? "Selecione um estado no mapa ou use o menu de estado." : "Selecione o Brasil no globo para começar.";
  const options = ready ? [...admin.current.stateNames] : [...new Map(packages.map((p) => [p.uf, p.ufName]))];
  options.sort((a, b) => a[1].localeCompare(b[1], "pt-BR"));
  const openCity = (entry) => go({ country: "BRA", uf: entry.uf, city: entry.municipalityCode });
  return React.createElement("div", {
    className: "app",
    "data-level": level
  }, React.createElement("header", {
    className: "app-header"
  }, React.createElement("h1", {
    className: "app-title"
  }, "Gaiapus"), React.createElement("p", {
    className: "app-subtitle"
  }, "Exploração global · recortes validados no Brasil")), React.createElement("nav", {
    className: "nav",
    "aria-label": "Navegação por escala"
  }, React.createElement("button", {
    type: "button",
    className: "nav-back",
    onClick: () => go(steps.at(-2)?.[1] || {}),
    disabled: steps.length < 2
  }, "← Voltar"), React.createElement("ol", {
    className: "breadcrumb"
  }, steps.map(([name, step], i) => React.createElement("li", {
    key: i,
    className: "breadcrumb-item"
  }, React.createElement("button", {
    type: "button",
    className: "breadcrumb-link",
    "aria-current": i === steps.length - 1 ? "step" : undefined,
    onClick: () => go(step)
  }, name)))), country !== "BRA" ? React.createElement("button", {
    type: "button",
    className: "nav-back",
    onClick: () => go({ country: "BRA" })
  }, "Abrir Brasil") : React.createElement("label", {
    className: "nav-select"
  }, React.createElement("span", {
    className: "nav-select-label"
  }, "Estado"), React.createElement("select", {
    value: uf || "",
    onChange: (event) => go({ country: "BRA", uf: event.target.value || undefined })
  }, React.createElement("option", {
    value: ""
  }, "Selecione"), options.map(([code, name]) => React.createElement("option", {
    key: code,
    value: code
  }, name))))), React.createElement("main", {
    className: "layout"
  }, React.createElement("section", {
    className: "map-section",
    "aria-label": "Mapa"
  }, React.createElement("div", {
    ref: box,
    className: "map-canvas",
    role: "application",
    "aria-label": "Globo interativo. Alternativa: use Abrir Brasil e o menu Estado."
  }), err && React.createElement("p", {
    className: "map-error",
    role: "alert"
  }, err), React.createElement("p", {
    className: "map-hint",
    "aria-live": "polite"
  }, status), active && React.createElement("dl", {
    className: "scene-meta"
  }, React.createElement("dt", null, "Área"), React.createElement("dd", null, active.label, " · recorte demonstrativo"), React.createElement("dt", null, "Cena"), React.createElement("dd", null, data.image.scene_id), React.createElement("dt", null, "Data da imagem"), React.createElement("dd", null, data.image.date.split("-").reverse().join("/"), " · NASA HLS"))), active ? React.createElement("section", {
    className: "chart-section",
    "aria-labelledby": "chart-heading"
  }, React.createElement("h2", {
    id: "chart-heading",
    className: "section-title"
  }, "Vegetação e chuva · ", active.label), React.createElement("img", {
    className: "scene-fallback",
    src: data.image.url,
    alt: "Composição em cores naturais da cena HLS em " + active.label
  }), React.createElement("p", {
    className: "chart-note"
  }, data.validation.accepted_scenes, " cenas aceitas de ", data.validation.total_scenes, " · ", data.rainfall.length, " dias de chuva"), React.createElement(Chart, {
    observations: data.observations,
    rainfall: data.reference_rainfall
  }), React.createElement("ul", {
    className: "legend"
  }, React.createElement("li", {
    className: "legend-item legend-ndvi"
  }, "NDVI: HLS a 30 m, calculado dentro do recorte. Lacunas ficam sem pontos."), React.createElement("li", {
    className: "legend-item legend-rain"
  }, "Chuva no gráfico: arquivos diários IMERG Final V07B, estimativa regional em mm por dia UTC."), data.validation.rain_crosscheck && React.createElement("li", null, "Conferência IMERG Final V07B: ", data.validation.rain_crosscheck.matched_days, " de ", data.rainfall.length, " dias pareados", " · ", data.validation.rain_crosscheck.mismatched_days, " acima de 0,02 mm. Sem medição em campo.")), React.createElement("details", null, React.createElement("summary", null, "Ver observações e qualidade por cena"), React.createElement("div", {
    className: "table-wrap",
    tabIndex: 0,
    role: "region",
    "aria-label": "Tabela de observações"
  }, React.createElement("table", {
    className: "obs-table"
  }, React.createElement("caption", null, "Observações por cena"), React.createElement("thead", null, React.createElement("tr", null, React.createElement("th", {
    scope: "col"
  }, "Data"), React.createElement("th", {
    scope: "col"
  }, "NDVI (HLS)"), React.createElement("th", {
    scope: "col"
  }, "Pixels válidos"))), React.createElement("tbody", null, data.observations.map((o) => React.createElement("tr", {
    key: o.scene_id
  }, React.createElement("th", {
    scope: "row",
    title: o.scene_id
  }, o.date.split("-").reverse().join("/"), " · ", o.collection === "hls2-l30" ? "L30" : "S30"), React.createElement("td", null, num(o.ndvi, 2)), React.createElement("td", null, o.valid_fraction == null ? "—" : Math.round(o.valid_fraction * 100) + "%")))))))) : React.createElement("section", {
    className: "chart-section",
    "aria-labelledby": "chart-heading"
  }, React.createElement("h2", {
    id: "chart-heading",
    className: "section-title"
  }, uf && !matching.length ? "Sem dados validados" : "Recortes validados"), (!country || country === "BRA") && matching.length > 0 ? React.createElement("ul", {
    className: "sources"
  }, matching.map((entry) => React.createElement("li", {
    key: entry.id
  }, React.createElement("button", {
    type: "button",
    className: "nav-back",
    onClick: () => city ? open(entry) : openCity(entry)
  }, city ? "Abrir recorte de " : "", entry.label, " · ", entry.ufName)))) : React.createElement("p", {
    className: "chart-note"
  }, "Selecione uma área com pacote publicado para ver imagem, NDVI e chuva."))), React.createElement("footer", {
    className: "app-footer"
  }, React.createElement("h2", {
    className: "section-title"
  }, "Fontes"), React.createElement("ul", {
    className: "sources"
  }, React.createElement("li", null, React.createElement("a", {
    href: "https://planetarycomputer.microsoft.com/dataset/group/hls2"
  }, "NASA HLS v2 via Microsoft Planetary Computer")), React.createElement("li", null, React.createElement("a", {
    href: "https://doi.org/10.5067/GPM/IMERGDF/DAY/07"
  }, "NASA IMERG Final (diário)"), " · ", React.createElement("a", {
    href: "https://power.larc.nasa.gov/docs/methodology/data/sources_meteorology/"
  }, "NASA POWER / IMERG_PRECTOT")), React.createElement("li", null, React.createElement("a", {
    href: "https://servicodados.ibge.gov.br/api/docs/malhas?versao=4"
  }, "Limites administrativos do IBGE"))), React.createElement("p", {
    className: "disclaimer"
  }, "Série histórica e polígono demonstrativo. A chuva de satélite representa a região. NDVI é um indicador de vegetação ativa.")));
}

async function start() {
  const root = document.getElementById("root");
  try {
    const read = async (path) => {
      const response = await fetch(path);
      if (!response.ok) throw new Error("Unavailable: " + path);
      return response.json();
    };
    const manifest = await read("./data/regions.json");
    const results = await Promise.allSettled(manifest.map(async (entry) => {
      const [data, cityGeometry, image] = await Promise.all([
        read(entry.directory + "/field.json"), read(entry.directory + "/city.geojson"),
        fetch(entry.directory + "/scene.png"),
      ]);
      if (!image.ok || !image.headers.get("Content-Type")?.startsWith("image/png")) throw new Error("Missing scene");
      data.image.url = entry.directory + "/scene.png";
      return { ...entry, data, cityGeometry };
    }));
    const packages = validatedPackages(results.filter((item) => item.status === "fulfilled").map((item) => item.value));
    ReactDOM.createRoot(root).render(React.createElement(App, { packages }));
  } catch {
    root.textContent = "Não foi possível carregar as observações. Abra o aplicativo pelo servidor local.";
  }
}
start();
})();
