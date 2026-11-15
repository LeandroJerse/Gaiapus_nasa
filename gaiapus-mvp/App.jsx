import React from "react";

const STYLE = "./data/globe-style.json";
const HOME = { center: [-50, -15], zoom: 1.4 };
const DAY = 864e5;

function featureBounds(feature) {
  const result = [[Infinity, Infinity], [-Infinity, -Infinity]];
  function extend(coordinates) {
    if (typeof coordinates[0] !== "number") { coordinates.forEach(extend); return; }
    for (let axis = 0; axis < 2; axis += 1) {
      result[0][axis] = Math.min(result[0][axis], coordinates[axis]);
      result[1][axis] = Math.max(result[1][axis], coordinates[axis]);
    }
  }
  extend(feature.geometry.coordinates);
  return result;
}

const ts = (d) => Date.parse(d + "T00:00:00Z");
const br = (d) => d.slice(8, 10) + "/" + d.slice(5, 7);
const num = (v, n) => (v == null ? "sem dado" : v.toFixed(n).replace(".", ","));
const reduced = () => !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

function Chart({ observations, rainfall }) {
  const dates = observations.map((o) => o.date).concat(rainfall.map((r) => r.date));
  if (!dates.length) return <p className="chart-empty">Sem dados para o gráfico.</p>;
  const W = 720, L = 52, PW = 652, AT = 34, AH = 130, BT = 214, BH = 110, H = 364;
  const all = dates.map(ts);
  const t0 = Math.min(...all) - DAY, span = Math.max(...all) + DAY - t0;
  const x = (d) => L + ((ts(d) - t0) / span) * PW;
  const pts = observations.filter((o) => o.ndvi != null);
  const lo = Math.min(0, ...pts.map((o) => o.ndvi));
  const yN = (v) => AT + AH - ((v - lo) / (1 - lo)) * AH;
  const rmax = Math.max(1, ...rainfall.map((r) => r.rain_mm || 0));
  const yR = (v) => BT + BH - (v / rmax) * BH;
  const bw = Math.max(1, (PW / (span / DAY)) * 0.8);
  const ticks = [0, 1, 2, 3, 4].map((i) => new Date(t0 + DAY + ((span - 2 * DAY) * i) / 4).toISOString().slice(0, 10));
  const grid = (y, label, key) => (
    <g key={key} className="chart-tick">
      <line x1={L} x2={L + PW} y1={y} y2={y} stroke="currentColor" opacity="0.15" />
      <text x={L - 6} y={y + 4} textAnchor="end" fontSize="11" fill="currentColor">{label}</text>
    </g>
  );
  return (
    <svg className="chart-svg" viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-labelledby="chart-title chart-desc" preserveAspectRatio="xMidYMid meet">
      <title id="chart-title">NDVI por cena e chuva diária</title>
      <desc id="chart-desc">Dois painéis com o mesmo eixo de datas. Os valores estão na tabela abaixo.</desc>
      <g className="chart-panel chart-panel-ndvi">
        <text x={L} y={AT - 12} fontSize="13" fontWeight="600" fill="currentColor">NDVI por cena (HLS, recorte)</text>
        {[0, 0.5, 1].map((v) => grid(yN(v), num(v, 1), v))}
        {pts.map((o) => (
          <circle key={o.scene_id} className="chart-point" cx={x(o.date)} cy={yN(o.ndvi)} r="4.5" fill="#86efac">
            <title>{`${br(o.date)}: NDVI ${num(o.ndvi, 2)}`}</title>
          </circle>
        ))}
      </g>
      <g className="chart-panel chart-panel-rain">
        <text x={L} y={BT - 12} fontSize="13" fontWeight="600" fill="currentColor">Chuva diária em mm (IMERG Final V07B)</text>
        {[0, rmax / 2, rmax].map((v) => grid(yR(v), num(v, 0), v))}
        {rainfall.filter((r) => r.rain_mm != null).map((r) => (
          <rect key={r.date} className="chart-bar" x={x(r.date) - bw / 2} y={yR(r.rain_mm)} width={bw} height={BT + BH - yR(r.rain_mm)} fill="#7dd3fc">
            <title>{`${br(r.date)}: ${num(r.rain_mm, 1)} mm`}</title>
          </rect>
        ))}
      </g>
      <g className="chart-axis-x">
        {ticks.map((d) => (
          <g key={d}>
            <line x1={x(d)} x2={x(d)} y1={AT} y2={BT + BH} stroke="currentColor" opacity="0.1" />
            <text x={x(d)} y={BT + BH + 16} textAnchor="middle" fontSize="11" fill="currentColor">{br(d)}</text>
          </g>
        ))}
        <text x={L + PW / 2} y={H - 6} textAnchor="middle" fontSize="11" fill="currentColor">Data (dia/mês)</text>
      </g>
    </svg>
  );
}

export default function App({ packages }) {
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
  const go = (next) => { current.current = next; select(next); };
  const open = (entry) => go({ country: "BRA", uf: entry.uf, city: entry.municipalityCode, field: entry.id });
  const countries = country === "BRA" ? "Brasil" : selection.countryName || country;
  const ufName = admin.current?.stateNames.get(uf) || packages.find((entry) => entry.uf === uf)?.ufName || uf;
  const cityName = admin.current?.cityNames.get(city) || packages.find((entry) => entry.municipalityCode === city)?.label || city;
  const steps = [["Globo", {}]];
  if (country) steps.push([countries, { country, countryName: selection.countryName, center: selection.center }]);
  if (uf) steps.push([ufName, { country, uf }]);
  if (city) steps.push([cityName, { country, uf, city }]);
  if (field) steps.push(["Recorte demonstrativo", selection]);

  React.useEffect(() => {
    const gl = window.maplibregl;
    if (!gl || !box.current) {
      setErr("Globo indisponível. Os recortes podem ser abertos pelo seletor.");
      return undefined;
    }
    let dead = false;
    let map;
    try {
      map = new gl.Map({ container: box.current, style: STYLE, ...HOME,
        attributionControl: { compact: false, customAttribution: "Estilo: MapLibre demotiles" } });
    } catch {
      setErr("Globo 3D indisponível. Os recortes podem ser abertos pelo seletor.");
      return undefined;
    }
    mapRef.current = map;
    map.addControl(new gl.NavigationControl(), "top-right");
    map.on("error", (event) => console.warn("map", event.error));
    map.on("load", async () => {
      const read = async (name) => {
        const response = await fetch("./data/" + name);
        if (!response.ok) throw new Error(name);
        return response.json();
      };
      try {
        const [brazil, states, minasCities, stateNames, cityNames] = await Promise.all([
          read("BR.geojson"), read("states.geojson"), read("mg-cities.geojson"),
          read("state-names.json"), read("mg-city-names.json"),
        ]);
        if (dead) return;
        const cities = new Map(minasCities.features.map((feature) => [String(feature.properties.codarea), feature]));
        const names = new Map(cityNames.map(({ id, nome }) => [String(id), nome]));
        for (const entry of packages) {
          const feature = entry.cityGeometry.features.find((item) => String(item.properties.codarea) === entry.municipalityCode);
          cities.set(entry.municipalityCode, feature);
          names.set(entry.municipalityCode, entry.label);
        }
        const color = "#5eead4";
        const cityFeatures = { type: "FeatureCollection", features: [...cities].map(([code, feature]) => ({
          ...feature, properties: { ...feature.properties, code, uf: code.slice(0, 2), available: packages.some((p) => p.municipalityCode === code) },
        })) };
        admin.current = { brazil: brazil.features[0], states: new Map(states.features.map((f) => [String(f.properties.codarea), f])),
          cities, stateNames: new Map(stateNames.map(({ id, nome }) => [String(id), nome])), cityNames: names };
        for (const [id, collection] of [["country-br", brazil], ["states", states], ["cities", cityFeatures]]) {
          map.addSource(id, { type: "geojson", data: collection });
          map.addLayer({ id: id + "-fill", type: "fill", source: id, layout: { visibility: "none" },
            paint: { "fill-color": ["case", ["==", ["get", "available"], true], "#fcd34d", color],
              "fill-opacity": 0.2, "fill-outline-color": color } });
        }
        for (const entry of packages) {
          const { id, data } = entry;
          map.addSource("scene-" + id, { type: "image", url: data.image.url, coordinates: data.image.corners });
          map.addLayer({ id: "scene-" + id, type: "raster", source: "scene-" + id,
            layout: { visibility: "none" }, paint: { "raster-fade-duration": 0 } });
        }
        map.addSource("fields", { type: "geojson", data: { type: "FeatureCollection", features: packages.map((entry) => ({
          type: "Feature", properties: { id: entry.id, city: entry.municipalityCode }, geometry: entry.data.field.geojson,
        })) } });
        map.addLayer({ id: "fields-fill", type: "fill", source: "fields", layout: { visibility: "none" },
          paint: { "fill-color": "#f472b6", "fill-opacity": 0.2, "fill-outline-color": "#f472b6" } });
        map.addLayer({ id: "fields-line", type: "line", source: "fields", layout: { visibility: "none" },
          paint: { "line-color": "#f472b6", "line-width": 2 } });

        const pick = (point, layer) => map.getLayer(layer) ? map.queryRenderedFeatures(point, { layers: [layer] })[0] : undefined;
        map.on("click", (event) => {
          const state = current.current;
          if (!state.country) {
            if (pick(event.point, "country-br-fill")) { go({ country: "BRA" }); return; }
            const props = pick(event.point, "countries-fill")?.properties;
            if (props?.ADM0_A3 === "BRA") go({ country: "BRA" });
            else if (props?.ADM0_A3) go({ country: props.ADM0_A3,
              countryName: props.NAME || props.ADMIN, center: event.lngLat.toArray() });
            return;
          }
          if (state.country !== "BRA" || state.field) return;
          if (!state.uf) {
            const code = pick(event.point, "states-fill")?.properties?.codarea;
            if (code !== undefined) go({ country: "BRA", uf: String(code) });
          } else if (!state.city) {
            const code = pick(event.point, "cities-fill")?.properties?.code;
            if (code) go({ country: "BRA", uf: state.uf, city: String(code) });
          } else {
            const id = pick(event.point, "fields-fill")?.properties?.id;
            const entry = packages.find((item) => item.id === id && item.municipalityCode === state.city);
            if (entry) open(entry);
          }
        });
        setReady(true);
      } catch {
        if (!dead) setErr("Limites administrativos indisponíveis no mapa. Use o seletor para abrir os recortes.");
      }
    });
    return () => { dead = true; map.remove(); mapRef.current = null; };
  }, [packages]);

  React.useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const duration = reduced() ? 0 : 1150;
    const visibility = (id, flag) => map.setLayoutProperty(id, "visibility", flag ? "visible" : "none");
    visibility("country-br-fill", !country);
    visibility("states-fill", country === "BRA" && !uf);
    visibility("cities-fill", !!uf && !city);
    map.setFilter("cities-fill", ["==", ["get", "uf"], uf || ""]);
    visibility("fields-fill", !!city && !active);
    visibility("fields-line", !!city);
    for (const id of ["fields-fill", "fields-line"]) map.setFilter(id,
      active ? ["==", ["get", "id"], active.id] : ["==", ["get", "city"], city || ""]);
    for (const entry of packages) visibility("scene-" + entry.id, active?.id === entry.id);
    const boundary = city ? admin.current.cities.get(city) : uf ? admin.current.states.get(uf) : admin.current.brazil;
    const bounds = active?.data.bounds.field || (boundary && featureBounds(boundary));
    if (country === "BRA" && bounds?.flat().every(Number.isFinite)) map.fitBounds(bounds, { padding: 36, maxZoom: 15, duration });
    else map.easeTo(selection.center ? { center: selection.center, zoom: 3, duration } : { ...HOME, duration });
  }, [ready, selection, active, packages]);

  const status = active ? "A linha marca o recorte onde o NDVI foi calculado."
    : country && country !== "BRA" ? "Exploração global. A análise da competição está disponível somente em recortes publicados no Brasil."
    : uf && !matching.length ? "Ainda não há pacote validado nesta seleção."
    : city ? "Clique no recorte marcado ou abra pelo painel."
    : uf ? "Selecione um município com dados validados."
    : country ? "Selecione um estado no mapa ou use o menu de estado."
    : "Selecione o Brasil no globo para começar.";
  const options = ready ? [...admin.current.stateNames] : [...new Map(packages.map((p) => [p.uf, p.ufName]))];
  options.sort((a, b) => a[1].localeCompare(b[1], "pt-BR"));
  const openCity = (entry) => go({ country: "BRA", uf: entry.uf, city: entry.municipalityCode });

  return (
    <div className="app" data-level={level}>
      <header className="app-header">
        <h1 className="app-title">Gaiapus</h1>
        <p className="app-subtitle">Exploração global · recortes validados no Brasil</p>
      </header>
      <nav className="nav" aria-label="Navegação por escala">
        <button type="button" className="nav-back" onClick={() => go(steps.at(-2)?.[1] || {})} disabled={steps.length < 2}>← Voltar</button>
        <ol className="breadcrumb">
          {steps.map(([name, step], i) => <li key={i} className="breadcrumb-item"><button type="button"
            className="breadcrumb-link" aria-current={i === steps.length - 1 ? "step" : undefined} onClick={() => go(step)}>{name}</button></li>)}
        </ol>
        {country !== "BRA" ? <button type="button" className="nav-back" onClick={() => go({ country: "BRA" })}>Abrir Brasil</button> :
          <label className="nav-select"><span className="nav-select-label">Estado</span>
            <select value={uf || ""} onChange={(event) => go({ country: "BRA", uf: event.target.value || undefined })}>
              <option value="">Selecione</option>
              {options.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
            </select></label>}
      </nav>
      <main className="layout">
        <section className="map-section" aria-label="Mapa">
          <div ref={box} className="map-canvas" role="application" aria-label="Globo interativo. Alternativa: use Abrir Brasil e o menu Estado." />
          {err && <p className="map-error" role="alert">{err}</p>}
          <p className="map-hint" aria-live="polite">{status}</p>
          {active && <dl className="scene-meta">
            <dt>Área</dt><dd>{active.label} · recorte demonstrativo</dd>
            <dt>Cena</dt><dd>{data.image.scene_id}</dd>
            <dt>Data da imagem</dt><dd>{data.image.date.split("-").reverse().join("/")} · NASA HLS</dd>
          </dl>}
        </section>
        {active ? <section className="chart-section" aria-labelledby="chart-heading">
          <h2 id="chart-heading" className="section-title">Vegetação e chuva · {active.label}</h2>
          <img className="scene-fallback" src={data.image.url} alt={"Composição em cores naturais da cena HLS em " + active.label} />
          <p className="chart-note">{data.validation.accepted_scenes} cenas aceitas de {data.validation.total_scenes} · {data.rainfall.length} dias de chuva</p>
          <Chart observations={data.observations} rainfall={data.reference_rainfall} />
          <ul className="legend">
            <li className="legend-item legend-ndvi">NDVI: HLS a 30 m, calculado dentro do recorte. Lacunas ficam sem pontos.</li>
            <li className="legend-item legend-rain">Chuva no gráfico: arquivos diários IMERG Final V07B, estimativa regional em mm por dia UTC.</li>
            {data.validation.rain_crosscheck && <li>
              Conferência IMERG Final V07B: {data.validation.rain_crosscheck.matched_days} de {data.rainfall.length} dias pareados
              {" · "}{data.validation.rain_crosscheck.mismatched_days} acima de 0,02 mm. Sem medição em campo.
            </li>}
          </ul>
          <details><summary>Ver observações e qualidade por cena</summary><div className="table-wrap" tabIndex={0} role="region" aria-label="Tabela de observações">
            <table className="obs-table"><caption>Observações por cena</caption>
              <thead><tr><th scope="col">Data</th><th scope="col">NDVI (HLS)</th><th scope="col">Pixels válidos</th></tr></thead>
              <tbody>{data.observations.map((o) => <tr key={o.scene_id}>
                <th scope="row" title={o.scene_id}>{o.date.split("-").reverse().join("/")} · {o.collection === "hls2-l30" ? "L30" : "S30"}</th>
                <td>{num(o.ndvi, 2)}</td><td>{o.valid_fraction == null ? "—" : Math.round(o.valid_fraction * 100) + "%"}</td>
              </tr>)}</tbody>
            </table></div></details>
        </section> : <section className="chart-section" aria-labelledby="chart-heading">
          <h2 id="chart-heading" className="section-title">{uf && !matching.length ? "Sem dados validados" : "Recortes validados"}</h2>
          {(!country || country === "BRA") && matching.length > 0 ? <ul className="sources">{matching.map((entry) => <li key={entry.id}>
            <button type="button" className="nav-back" onClick={() => city ? open(entry) : openCity(entry)}>{city ? "Abrir recorte de " : ""}{entry.label} · {entry.ufName}</button>
          </li>)}</ul> : <p className="chart-note">Selecione uma área com pacote publicado para ver imagem, NDVI e chuva.</p>}
        </section>}
      </main>
      <footer className="app-footer">
        <h2 className="section-title">Fontes</h2>
        <ul className="sources">
          <li><a href="https://planetarycomputer.microsoft.com/dataset/group/hls2">NASA HLS v2 via Microsoft Planetary Computer</a></li>
          <li><a href="https://doi.org/10.5067/GPM/IMERGDF/DAY/07">NASA IMERG Final (diário)</a> · <a href="https://power.larc.nasa.gov/docs/methodology/data/sources_meteorology/">NASA POWER / IMERG_PRECTOT</a></li>
          <li><a href="https://servicodados.ibge.gov.br/api/docs/malhas?versao=4">Limites administrativos do IBGE</a></li>
        </ul>
        <p className="disclaimer">Série histórica e polígono demonstrativo. A chuva de satélite representa a região. NDVI é um indicador de vegetação ativa.</p>
      </footer>
    </div>
  );
}
