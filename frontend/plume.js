// Plume dilution tool (plan.md decisions 10-19; backend/ssm_pt/engine/plume.py).
// Shares the map, basemaps, current arrows and helpers ($, api, sleep, fmtUTC, fmtPacific, currents, addControl)
// with the particle tool in index.html. One source for now; the request already takes a list.
const Plume = (() => {
  const PCOLOR = { source: '#ffd60a', outfall: '#1d3557', receptor: '#7b2cbf' };
  const F_MIN = 1e-5, F_MAX = 1e-1;  // colour scale: dilution 1:100,000 (lightest) to 1:10 (darkest)
  // Single-hue orange ramp, light -> dark (blue would vanish into the ocean basemap)
  const RAMP = ['#fde4d3', '#f9c3a0', '#f49a69', '#eb6834', '#c94f1f', '#9c3a12', '#6b2507'];
  const MGD = 0.0438126;  // m3/s per million US gallons a day

  const style = document.createElement('style');
  style.textContent = `
    .plume-raster { image-rendering: pixelated; }
    .colorbar canvas { display: block; width: 180px; height: 10px; margin: 3px 0 1px; border-radius: 2px; }
    .colorbar .ticks { display: flex; justify-content: space-between; width: 180px; font-size: 10px; color: #555; font-variant-numeric: tabular-nums; }
    .chart { margin-top: 6px; }
    .chart .title { font-weight: 600; margin-bottom: 2px; }
    .chart svg { display: block; width: 100%; height: 120px; overflow: visible; }
    .chart text { font-size: 9.5px; fill: #555; font-variant-numeric: tabular-nums; }
    .sw-sq { display: inline-block; width: 9px; height: 9px; transform: rotate(45deg); border: 1px solid white; box-shadow: 0 0 0 0.5px #999; }`;
  document.head.appendChild(style);

  const P = $('mode-plume');
  P.innerHTML = `
  <details class="section" open>
    <summary>1 · Source</summary>
    <div class="section-body">
      <div id="pl-src" class="release-box">Click an outfall (◆) or the water to place the source.</div>
      <div class="field"><label for="pl-name">Name</label><input id="pl-name" type="text" maxlength="120" value="Source"></div>
      <div class="field"><label for="pl-flow">Flow (m³/s)</label><input id="pl-flow" type="number" min="0.0001" max="100" step="any" value="0.00164"></div>
      <div class="hint" id="pl-flow-hint"></div>
      <div class="field"><label for="pl-nf">Near field</label>
        <select id="pl-nf"><option value="plumes" selected>PLUMES, every hour</option><option value="fixed">Fixed values</option></select>
      </div>
      <div id="pl-nf-plumes">
        <div class="field"><label for="pl-ports">Ports</label><input id="pl-ports" type="number" min="1" max="500" step="1" value="25"></div>
        <div class="field"><label for="pl-pd">Port diameter (mm)</label><input id="pl-pd" type="number" min="1" max="5000" step="0.1" value="12.7"></div>
        <div class="field"><label for="pl-sp">Port spacing (m)</label><input id="pl-sp" type="number" min="0" max="100" step="0.01" value="0.61"></div>
        <div class="field"><label for="pl-va">Jet angle up (°)</label><input id="pl-va" type="number" min="-90" max="90" step="5" value="45"></div>
        <div class="field"><label for="pl-brg">Jets point to (° true)</label><input id="pl-brg" type="number" min="0" max="359" step="1" placeholder="downstream"></div>
        <div class="field"><label for="pl-pdep">Port depth (m below MSL)</label><input id="pl-pdep" type="number" min="0.1" max="500" step="0.1" value="2"></div>
        <div class="field"><label for="pl-es">Effluent salinity</label><input id="pl-es" type="number" min="0" max="60" step="0.1" placeholder="as at the port"></div>
        <div class="field"><label for="pl-et">Effluent temp. (°C)</label><input id="pl-et" type="number" min="-2" max="60" step="0.1" placeholder="as at the port"></div>
        <div class="hint" id="pl-nf-hint"></div>
        <div class="hint">PLUMES' near-field model (UM3, through Ebb Carbon's plumes2) runs once per hour on the model's current, temperature and salinity at the source, in the Sequim Bay box only. Defaults are Ebb's Macoma diffuser, a placeholder until PNNL's values arrive. Blank jet direction: downstream each hour. Blank effluent: seawater intake, the same as the water at the port.</div>
      </div>
      <div id="pl-nf-fixed" hidden>
        <div class="field"><label for="pl-s0">Near-field dilution</label><input id="pl-s0" type="number" min="1" max="10000" step="0.5" value="8"></div>
        <div class="field"><label for="pl-diam">Plume diameter (m)</label><input id="pl-diam" type="number" min="0.1" max="1000" step="0.1" value="1.6"></div>
        <div class="hint">From your own near-field run (PLUMES). Defaults are the Admiralty Inlet TD1 case in Savoie et al. The near-field dilution caps how concentrated the plume can get; the diameter sets the release spot.</div>
      </div>
      <div class="field"><label for="pl-ta">Excess TA (µmol/kg)</label><input id="pl-ta" type="number" min="0" max="100000" step="100" value="5500"></div>
      <div class="field"><label for="pl-dic">Excess DIC (µmol/kg)</label><input id="pl-dic" type="number" min="0" max="100000" step="100" value="2300"></div>
      <div class="hint">Effluent minus ambient. Defaults: the TD1 effluent (TA ~7,600, DIC 4,312) minus a typical Salish Sea ambient (~2,100 and ~2,000). Replace with measured values.</div>
    </div>
  </details>

  <details class="section" open>
    <summary>2 · Time</summary>
    <div class="section-body">
      <div class="slider-field">
        <div class="head"><label for="pl-start">Start</label><span id="pl-start-utc" class="utc"></span></div>
        <input id="pl-start" type="range" min="0" max="0" step="1" value="0">
        <div id="pl-start-local" class="local"></div>
      </div>
      <div class="slider-field">
        <div class="head"><label for="pl-end">End</label><span id="pl-end-utc" class="utc"></span></div>
        <input id="pl-end" type="range" min="0" max="0" step="1" value="0">
        <div id="pl-end-local" class="local"></div>
      </div>
      <div class="hint" id="pl-window"></div>
      <div class="field"><label for="pl-dtout">Save a map every</label>
        <select id="pl-dtout"><option value="30">30 min</option><option value="60" selected>60 min</option><option value="120">2 h</option><option value="360">6 h</option></select>
      </div>
      <div class="hint">The source discharges continuously from Start to End.</div>
    </div>
  </details>

  <details class="section" open>
    <summary>3 · Mixing</summary>
    <div class="section-body">
      <div class="field"><label for="pl-n">Particles</label><input id="pl-n" type="number" min="1000" max="500000" step="1000" value="20000"></div>
      <div class="hint">More particles, smoother maps at high dilution, longer runs.</div>
      <div class="field"><label for="pl-dt">Time step</label>
        <select id="pl-dt"><option value="30">30 s</option><option value="60">60 s</option><option value="120">2 min</option><option value="300" selected>5 min</option><option value="600">10 min</option></select>
      </div>
      <div class="hint">Sets the run time: 3 days takes about 20 s at 5 min and about 45 s at 60 s. In Sequim Bay, 5 min matched 60 s to within the particles' own randomness.</div>
      <div class="field"><label for="pl-kh">Diffusivity (m²/s)</label><input id="pl-kh" type="number" min="0" max="100" step="0.1" value="1"></div>
      <div class="field"><label for="pl-col">Mixed through</label>
        <select id="pl-col"><option value="full" selected>Whole water column</option><option value="surface">Surface layer</option></select>
      </div>
      <div class="field" id="pl-f-mix" hidden><label for="pl-mix">Layer thickness (m)</label><input id="pl-mix" type="number" min="0.5" max="500" step="0.5" value="5"></div>
      <div class="hint" id="pl-col-hint"></div>
      <div class="field"><label for="pl-cell">Map cell (m)</label><input id="pl-cell" type="number" min="50" max="2000" step="50" value="150"></div>
      <div class="field"><label for="pl-span">Map size (km)</label><input id="pl-span" type="number" min="2" max="200" step="1" value="20"></div>
    </div>
  </details>

  <button id="pl-run" class="run" disabled>Run</button>
  <div id="pl-status" class="status"></div>

  <details id="pl-results" class="section" open hidden>
    <summary>Result</summary>
    <div class="hint" id="pl-kind"></div>
    <div class="stat-grid">
      <div class="stat-card"><div class="label">Lowest dilution</div><div class="num" id="pl-s-min">–</div><div class="unit">any cell, any time</div></div>
      <div class="stat-card"><div class="label">Released</div><div class="num" id="pl-s-rel">–</div><div class="unit">m³ of effluent</div></div>
      <div class="stat-card"><div class="label">Still on the map</div><div class="num" id="pl-s-on">–</div><div class="unit">of the effluent, at the end</div></div>
      <div class="stat-card"><div class="label">Particles</div><div class="num" id="pl-s-n">–</div><div class="unit">released</div></div>
    </div>
    <div class="field" style="margin-top:8px"><label for="pl-q">Show</label>
      <select id="pl-q"><option value="dil" selected>Dilution</option><option value="ta">Excess TA</option><option value="dic">Excess DIC</option></select>
    </div>
    <div class="field"><label for="pl-view">Map</label>
      <select id="pl-view"><option value="frame" selected>Each saved time</option><option value="max">Lowest dilution over the run</option><option value="mean">Mean over the run</option></select>
    </div>
    <div class="hint" id="pl-hover">Hover the map for values. Shift-click to add a receptor point.</div>
    <div id="pl-nf-res" hidden>
      <div class="chart"><div class="title">Near-field dilution (PLUMES, each hour)</div><svg id="pl-nf-svg"></svg></div>
      <div class="hint" id="pl-nf-sum"></div>
    </div>
    <div class="chart" id="pl-rec" hidden><div class="title" id="pl-rec-title"></div><svg id="pl-rec-svg"></svg></div>
    <div class="chart"><div class="title">Effluent still on the map (m³)</div><svg id="pl-build-svg"></svg>
      <div class="hint">Levels off once the area flushes as fast as the source fills it; still rising means the run is too short to reach a steady state.</div></div>
  </details>

  <details class="about">
    <summary>About this tool</summary>
    <ul>
      <li>Currents: NOAA SSCOFS nowcast, hourly, averaged over the 10 model layers. The arrows show that depth average.</li>
      <li>The effluent is assumed mixed through the whole water column (or the surface layer you set). Where the water is stratified this overstates dilution: treat the map as the most dilute case.</li>
      <li>Dilution in each map cell is the effluent volume there over the water volume, from counting particles. Cells with few particles are noisy, mostly at high dilution.</li>
      <li>Near field: PLUMES' UM3 run every hour (Ebb Carbon's plumes2, a Python port checked against the PLUMES2.0 program) on the model's current, temperature and salinity profile at the source, or fixed values from your own PLUMES run. Its dilution caps how concentrated the map can get. Dilution at the mixing-zone edges (metres from the port) is far below the model's ~150 m cells and is not shown on the map.</li>
      <li>Excess TA and DIC scale with dilution (they mix conservatively). pH and saturation state are not computed yet; CO₂ uptake from the air is not modelled (the Salish Sea Model does that).</li>
      <li>Diffusivity is not calibrated against observations: a screening tool, not a permit model.</li>
      <li>Data: <span id="pl-about-window">loading…</span>.</li>
    </ul>
  </details>`;

  const player = L.DomUtil.create('div', 'card player', $('main'));
  player.innerHTML = `<button title="Play / pause">▶</button><input type="range" min="0" max="0" value="0">
    <div class="time"><div class="utc"></div><div class="local"></div></div>`;
  const [playBtn, slider, timeBox] = player.children;
  L.DomEvent.disableClickPropagation(player);
  L.DomEvent.disableScrollPropagation(player);

  // ── State ──
  let ph = [], meta = null, source = null, R = null, view = null, frame = 0, timer = null, receptor = null;
  const frames = new Map();  // frame key -> Float32Array of effluent fraction, row-major from the south
  const outfalls = L.layerGroup();
  const sourceMark = L.circleMarker([0, 0], { radius: 7, weight: 2, color: '#1a1a2e', fillColor: PCOLOR.source, fillOpacity: 1, interactive: false });
  const receptorMark = L.circleMarker([0, 0], { radius: 5, weight: 2, color: 'white', fillColor: PCOLOR.receptor, fillOpacity: 1, interactive: false });
  let raster = null;

  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const sig = (x, n = 3) => Number(x.toPrecision(n)).toLocaleString('en-US');
  const fmtDil = f => f > 0 ? `1 : ${sig(1 / f)}` : '–';
  const shortDil = f => { const n = 1 / f; return `1:${n >= 1e6 ? `${sig(n / 1e6)}M` : n >= 1e4 ? `${sig(n / 1e3)}k` : sig(n)}`; };
  const setStatus = (msg, error = false) => { $('pl-status').textContent = msg; $('pl-status').classList.toggle('error', error); };

  // ── Colour ──
  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const RGB = RAMP.map(hex);
  function colour(f) {  // -> [r, g, b, a] or null below the scale
    if (!(f >= F_MIN)) return null;
    const t = Math.min(1, (Math.log10(f) - Math.log10(F_MIN)) / (Math.log10(F_MAX) - Math.log10(F_MIN)));
    const x = t * (RGB.length - 1), i = Math.min(RGB.length - 2, Math.floor(x)), w = x - i;
    const c = RGB[i].map((v, k) => Math.round(v + w * (RGB[i + 1][k] - v)));
    return [...c, Math.round(255 * (0.55 + 0.4 * t))];  // the faintest plume stays see-through
  }

  // ── Legend: colour bar for the shown quantity, plus the map symbols ──
  const legend = L.DomUtil.create('div', 'card legend colorbar');
  const legendCtl = L.control({ position: 'bottomleft' });
  legendCtl.onAdd = () => legend;
  function drawLegend() {
    const q = $('pl-q').value, s = source || {};
    const scale = { dil: null, ta: +$('pl-ta').value, dic: +$('pl-dic').value }[q];
    const fs = [F_MIN, 1e-4, 1e-3, 1e-2, F_MAX];
    const tick = f => scale === null ? (f === F_MIN ? '1:100k' : f === 1e-4 ? '1:10k' : `1:${sig(1 / f)}`) : sig(f * scale, 2);
    legend.innerHTML = `<h4>${{ dil: 'Dilution (1 : N)', ta: 'Excess TA (µmol/kg)', dic: 'Excess DIC (µmol/kg)' }[q]}</h4>
      <canvas width="180" height="1"></canvas><div class="ticks">${fs.map(f => `<span>${tick(f)}</span>`).join('')}</div>
      <div class="hint" style="margin:3px 0 4px">${scale === null ? 'Darker = less diluted' : 'Darker = more effluent'}; clear below 1:100,000</div>
      <div class="legend-item"><span class="legend-swatch"><span class="sw-dot" style="background:${PCOLOR.source};width:12px;height:12px"></span></span>Source</div>
      <div class="legend-item"><span class="legend-swatch"><span class="sw-sq" style="background:${PCOLOR.outfall}"></span></span>Outfall or candidate site</div>
      <div class="legend-item"><span class="legend-swatch"><span class="sw-dot" style="background:${PCOLOR.receptor};width:9px;height:9px"></span></span>Receptor point</div>
      <div class="legend-item"><span class="legend-swatch"><svg width="14" height="10"><path d="M1 5H12M8.5 2L12 5L8.5 8" stroke="#1d3557" stroke-width="1.3" fill="none"/></svg></span>Depth-averaged current</div>`;
    const c = legend.querySelector('canvas'), ctx = c.getContext('2d');
    for (let x = 0; x < 180; x++) {
      const [r, g, b, a] = colour(10 ** (Math.log10(F_MIN) + (x + 0.5) / 180 * (Math.log10(F_MAX) - Math.log10(F_MIN))));
      ctx.fillStyle = `rgba(${r},${g},${b},${a / 255})`;
      ctx.fillRect(x, 0, 1, 1);
    }
  }

  // ── Data window and outfalls (loaded the first time the tool is shown) ──
  async function loadMeta() {
    try { meta = await api('GET', 'plume/meta'); } catch (e) { return setStatus('Cannot reach the backend: ' + e.message, true); }
    ph = meta.hours.map(h => new Date(h));
    if (!ph.length) return setStatus('No plume data on disk yet: run scripts/fetch_3d.py.', true);
    $('pl-start').max = $('pl-end').max = ph.length - 1;
    $('pl-end').value = Math.min(ph.length - 1, 72);  // three days by default
    $('pl-about-window').textContent = `${fmtUTC(ph[0])} to ${fmtUTC(ph[ph.length - 1])}`;
    updateTimes();
    $('pl-run').disabled = !source;
    if (meta.outfalls) loadOutfalls();
  }
  async function loadOutfalls() {
    const g = await api('GET', 'outfalls').catch(() => null);
    if (!g) return;
    for (const f of g.features) {
      const p = f.properties, [lon, lat] = f.geometry.coordinates;
      const flow = p.flow_mgd ? `${sig(p.flow_mgd)} MGD (${sig(p.flow_m3s)} m³/s)` : 'flow not listed';
      L.marker([lat, lon], {
        icon: L.divIcon({ className: '', html: `<span class="sw-sq" style="background:${PCOLOR.outfall};width:8px;height:8px"></span>`, iconSize: [10, 10] }),
        keyboard: false,
      }).bindTooltip(`<b>${esc(p.name)}</b><br>${esc(p.kind)}${p.npdes_id ? ' · ' + esc(p.npdes_id) : ''}<br>${flow}<br>`
        + `Model depth ${p.depth_m} m; listed position ${p.snap_m} m away`, { direction: 'top', offset: [0, -4] })
        .on('click', e => { L.DomEvent.stopPropagation(e); setSource(lon, lat, p); })
        .addTo(outfalls);
    }
  }

  // ── Source and receptor ──
  function setSource(lon, lat, p = null) {
    source = { lon: +lon.toFixed(5), lat: +lat.toFixed(5) };
    sourceMark.setLatLng([lat, lon]).addTo(map);
    if (p) {
      $('pl-name').value = p.name.slice(0, 120);
      if (p.flow_m3s) $('pl-flow').value = p.flow_m3s;
    }
    $('pl-src').classList.add('set');
    $('pl-src').innerHTML = `<b>${esc($('pl-name').value)}</b> at <span class="coords">${source.lat.toFixed(4)}° N, ${(-source.lon).toFixed(4)}° W</span>`
      + (p ? `<br>${esc(p.kind)}. Snapped ${p.snap_m} m to the model mesh (${p.depth_m} m deep).`
        + (p.flow_m3s ? '' : ' No flow on record: enter one.') : '')
      + '<br>Click again to move it.';
    updateFlowHint(p);
    $('pl-run').disabled = !ph.length;
  }
  function updateFlowHint(p) {
    const q = +$('pl-flow').value;
    $('pl-flow-hint').textContent = `${sig(q * 3600)} m³/h, ${sig(q / MGD)} MGD.` + (p?.flow_mgd ? ` Listed design flow ${sig(p.flow_mgd)} MGD.`
      : source && !p?.flow_m3s ? ' Placeholder (Ebb\'s Macoma discharge until set): enter the outfall\'s flow.' : '');
    updateNearField();
  }
  $('pl-flow').addEventListener('input', () => updateFlowHint(null));

  const plumes = () => $('pl-nf').value === 'plumes';
  const optional = id => $(id).value === '' ? null : +$(id).value;
  function updateNearField() {
    $('pl-nf-plumes').hidden = !plumes();
    $('pl-nf-fixed').hidden = plumes();
    const v = +$('pl-flow').value / +$('pl-ports').value / (Math.PI * (+$('pl-pd').value / 2000) ** 2);
    $('pl-nf-hint').textContent = Number.isFinite(v) ? `Jet exit speed ${sig(v, 2)} m/s per port.`
      + (v > 10 ? ' Very fast: check the flow and port size (PLUMES has driven such jets through the surface).' : '') : '';
  }
  ['pl-ports', 'pl-pd'].forEach(id => $(id).addEventListener('input', updateNearField));
  $('pl-nf').addEventListener('change', updateNearField);
  updateNearField();
  $('pl-name').addEventListener('input', () => { if (source) $('pl-src').querySelector('b').textContent = $('pl-name').value; });

  async function setReceptor(latlng) {
    receptor = latlng;
    receptorMark.setLatLng(latlng).addTo(map);
    try {
      const r = await api('GET', `plumes/${R.id}/receptor?lon=${latlng.lng}&lat=${latlng.lat}`);
      receptor.series = r.fraction;
      drawReceptor();
    } catch (e) {
      receptorMark.remove();
      receptor = null;
      $('pl-rec').hidden = true;
      $('pl-hover').textContent = e.message;
    }
  }

  function click(e) {
    if (e.originalEvent.shiftKey && R) return setReceptor(e.latlng);
    setSource(e.latlng.lng, e.latlng.lat);
  }

  // ── Time sliders ──
  const durationH = () => (ph[+$('pl-end').value] - ph[+$('pl-start').value]) / 3.6e6;
  function updateTimes(e) {
    let s = +$('pl-start').value, en = +$('pl-end').value;
    if (s >= en) {
      if (e?.target.id === 'pl-end') { en = Math.max(en, 1); s = en - 1; } else { s = Math.min(s, ph.length - 2); en = s + 1; }
      $('pl-start').value = s;
      $('pl-end').value = en;
    }
    for (const [id, i] of [['pl-start', s], ['pl-end', en]]) {
      $(id + '-utc').textContent = fmtUTC(ph[i]).slice(5);
      $(id + '-local').textContent = fmtPacific.format(ph[i]);
    }
    if (!R) currents.setTime(ph[s]);
    const d = durationH();
    $('pl-window').textContent = `Duration ${d} h (${sig(d / 24, 2)} days). Data on disk: ${$('pl-about-window').textContent}.`;
  }
  $('pl-start').addEventListener('input', updateTimes);
  $('pl-end').addEventListener('input', updateTimes);
  $('pl-col').addEventListener('change', updateColumn);
  function updateColumn() {
    const surface = $('pl-col').value === 'surface';
    $('pl-f-mix').hidden = !surface;
    $('pl-col-hint').textContent = surface
      ? 'Effluent stays in the top layer (or the whole depth where shallower). Still moved by the depth-averaged current.'
      : 'Effluent fills the water column under it: the most dilute case.';
  }
  updateColumn();

  // ── Run ──
  $('pl-run').addEventListener('click', async () => {
    pause();
    $('pl-run').disabled = true;
    const body = {
      sources: [{
        name: $('pl-name').value || 'Source', lon: source.lon, lat: source.lat, flow_m3s: +$('pl-flow').value,
        near_field_dilution: +$('pl-s0').value, plume_diameter_m: +$('pl-diam').value,
        excess_ta: +$('pl-ta').value, excess_dic: +$('pl-dic').value,
        diffuser: plumes() ? {
          n_ports: +$('pl-ports').value, port_diameter_m: +$('pl-pd').value / 1000, port_spacing_m: +$('pl-sp').value,
          vertical_angle_deg: +$('pl-va').value, bearing_deg: optional('pl-brg'), port_depth_m: +$('pl-pdep').value,
          effluent_salinity: optional('pl-es'), effluent_temperature_c: optional('pl-et'),
        } : null,
      }],
      start: ph[+$('pl-start').value].toISOString(),
      duration_h: durationH(),
      n_particles: +$('pl-n').value,
      diffusivity_m2s: +$('pl-kh').value,
      mixing_depth_m: $('pl-col').value === 'surface' ? +$('pl-mix').value : null,
      cell_m: +$('pl-cell').value,
      span_km: +$('pl-span').value,
      output_interval_min: +$('pl-dtout').value,
      dt_s: +$('pl-dt').value,
    };
    const t0 = Date.now(), secs = () => Math.round((Date.now() - t0) / 1000);
    try {
      setStatus('Submitting…');
      let s = await api('POST', 'plumes', body);
      while (s.status === 'queued' || s.status === 'running') {
        setStatus(`${s.status === 'queued' ? 'Waiting for a worker' : (body.sources[0].diffuser ? `PLUMES for each hour, then ` : '')
          + `${body.duration_h} h with ${sig(body.n_particles)} particles`}… ${secs()} s`);
        await sleep(2000);
        s = await api('GET', `plumes/${s.id}`);
      }
      if (s.status === 'failed') throw new Error(s.error);
      showResult(s.id, await api('GET', `plumes/${s.id}/result`), body);
      setStatus(`Done in ${secs()} s. Press ▶ to replay, drag the slider, hover for values.`);
    } catch (e) {
      setStatus(e.message, true);
    } finally {
      $('pl-run').disabled = !source;
    }
  });

  // ── Result ──
  async function showResult(id, m, body) {
    R = { ...m, id, body, dates: m.times.map(t => new Date(t)) };
    frames.clear();
    receptor = null;
    receptorMark.remove();
    $('pl-rec').hidden = true;
    const s = body.sources[0], nf = R.near_field?.[0];
    const nfMedian = nf ? median(nf.dilution) : null;
    $('pl-nf-res').hidden = !nf;
    if (nf) {
      const range = (a, n = 2) => { const v = a.filter(x => x !== null); return `${sig(Math.min(...v), n)}–${sig(Math.max(...v), n)}`; };
      $('pl-nf-sum').textContent = `Median 1 : ${sig(nfMedian)}, range 1 : ${range(nf.dilution, 3)}. Where the near field ends the plume `
        + `is ${range(nf.trap_depth_m)} m below the surface and ${range(nf.width_m)} m wide; current at the port ${range(nf.current_m_s)} m/s.`
        + (nf.failed.length ? ` Failed for ${nf.failed.length} of ${nf.times.length} hours (bridged from the hours around them), first: ${nf.failed[0]}.` : '')
        + (nf.warnings.length ? ` plumes2 warned: ${nf.warnings.join(' · ')}` : '');
    }
    $('pl-kind').textContent = `${s.name}: ${sig(s.flow_m3s)} m³/s, `
      + (nf ? `near field from PLUMES each hour (median 1 : ${sig(nfMedian)}), ` : `near-field dilution ${s.near_field_dilution}, `)
      + `${body.mixing_depth_m ? `top ${body.mixing_depth_m} m` : 'whole water column'}, ${R.cell_m} m cells, ${body.dt_s} s steps.`;
    $('pl-s-min').textContent = R.min_dilution ? `1 : ${sig(R.min_dilution)}` : '–';
    $('pl-s-rel').textContent = sig(R.released_m3[R.released_m3.length - 1]);
    $('pl-s-on').textContent = `${Math.round(R.on_grid_pct[R.on_grid_pct.length - 1])}%`;
    $('pl-s-n').textContent = sig(R.n_particles);
    $('pl-results').hidden = false;
    slider.max = R.times.length - 1;
    drawBuildUp();
    const b = L.latLngBounds(R.bounds);
    if (!map.getBounds().contains(b)) map.fitBounds(b);
    view = $('pl-view').value;
    player.style.display = view === 'frame' ? 'flex' : 'none';
    await setFrame(0);
    if (view === 'frame') play();
  }

  async function getFrame(key) {
    if (frames.has(key)) return frames.get(key);
    const d = await api('GET', `plumes/${R.id}/frame/${key}`);
    const a = new Float32Array(R.rows * R.cols);
    d.idx.forEach((k, i) => { a[k] = d.val[i]; });
    frames.set(key, a);
    return a;
  }

  let shown = null;  // the array on the map
  async function setFrame(k) {
    frame = k;
    slider.value = k;
    const key = view === 'frame' ? k : view;
    const a = await getFrame(key);
    if (key !== (view === 'frame' ? frame : view)) return;  // overtaken by a newer frame
    shown = a;
    drawRaster(a);
    const t = R.dates[k], dh = (t - R.dates[0]) / 3.6e6;
    timeBox.children[0].textContent = `${fmtUTC(t)} · +${dh.toFixed(0)} h`;
    timeBox.children[1].textContent = fmtPacific.format(t);
    currents.setTime(t);
    if (receptor?.series) drawReceptor();
    if (R.near_field?.[0]) drawNearField();
    if (view === 'frame' && timer && k + 1 < R.times.length) getFrame(k + 1);  // prefetch while playing
  }

  function drawRaster(a) {
    const c = document.createElement('canvas');
    c.width = R.cols;
    c.height = R.rows;
    const ctx = c.getContext('2d'), img = ctx.createImageData(R.cols, R.rows);
    for (let k = 0; k < a.length; k++) {
      const rgba = colour(a[k]);
      if (!rgba) continue;
      const r = Math.floor(k / R.cols), col = k % R.cols, o = 4 * ((R.rows - 1 - r) * R.cols + col);  // row 0 is the south edge
      img.data.set(rgba, o);
    }
    ctx.putImageData(img, 0, 0);
    if (raster) raster.setUrl(c.toDataURL()).setBounds(L.latLngBounds(R.bounds));
    else raster = L.imageOverlay(c.toDataURL(), R.bounds, { className: 'plume-raster', interactive: false }).addTo(map);
  }

  const qScale = () => ({ dil: null, ta: R.body.sources[0].excess_ta, dic: R.body.sources[0].excess_dic })[$('pl-q').value];
  const fmtQ = f => {
    const s = qScale();
    return s === null ? fmtDil(f) : f > 0 ? `${sig(f * s, 2)} µmol/kg` : '–';
  };
  map.on('mousemove', e => {
    if (mode !== 'plume' || !R || !shown) return;
    const [[s, w], [n, ea]] = R.bounds, { lat, lng } = e.latlng;
    if (lat < s || lat > n || lng < w || lng > ea) return;
    const r = Math.floor((lat - s) / (n - s) * R.rows), c = Math.floor((lng - w) / (ea - w) * R.cols);
    const f = shown[r * R.cols + c];
    $('pl-hover').textContent = `Cursor: ${f > 0 ? `dilution ${fmtDil(f)} · excess TA ${sig(f * R.body.sources[0].excess_ta, 2)} µmol/kg` : 'no effluent'}`
      + (view === 'frame' ? '' : ` (${view} over the run)`) + '. Shift-click to add a receptor point.';
  });

  $('pl-q').addEventListener('change', () => { drawLegend(); if (receptor?.series) drawReceptor(); });
  $('pl-view').addEventListener('change', async () => {
    view = $('pl-view').value;
    pause();
    player.style.display = view === 'frame' ? 'flex' : 'none';
    if (R) await setFrame(frame);
  });

  function play() {
    if (!R || view !== 'frame') return;
    if (frame >= R.times.length - 1) setFrame(0);
    playBtn.textContent = '❚❚';
    timer = setInterval(() => {
      if (frame >= R.times.length - 1) return pause();
      setFrame(frame + 1);
    }, 250);
  }
  function pause() {
    clearInterval(timer);
    timer = null;
    playBtn.textContent = '▶';
  }
  playBtn.addEventListener('click', () => timer ? pause() : play());
  slider.addEventListener('input', e => { pause(); setFrame(+e.target.value); });

  // ── Charts: one line over the run's times, crosshair readout on hover ──
  function lineChart(svg, dates, ys, { log = false, fmt, mark = null }) {
    const W = svg.clientWidth || 280, H = 120, L0 = 44, B = 16, T0 = 6;
    const ok = ys.map(y => (log ? y > 0 : Number.isFinite(y)));
    const vals = ys.filter((y, i) => ok[i]);
    if (!vals.length) { svg.innerHTML = `<text x="${L0}" y="${H / 2}">No effluent here during the run</text>`; return; }
    const tf = log ? Math.log10 : y => y;
    let lo = Math.min(...vals.map(tf)), hi = Math.max(...vals.map(tf));
    if (log) { lo = Math.floor(lo); hi = Math.ceil(hi); } else { lo = Math.min(0, lo); }
    if (hi === lo) hi = lo + 1;
    const x = i => L0 + (W - L0 - 4) * (dates.length > 1 ? i / (dates.length - 1) : 0);
    const y = v => T0 + (H - T0 - B) * (1 - (tf(v) - lo) / (hi - lo));
    const ticks = log ? Array.from({ length: hi - lo + 1 }, (_, k) => 10 ** (lo + k)) : [lo, (lo + hi) / 2, hi];
    let d = '', pen = false;
    ys.forEach((v, i) => { if (ok[i]) { d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true; } else pen = false; });
    svg.innerHTML = ticks.map(t => `<line x1="${L0}" x2="${W - 4}" y1="${y(t)}" y2="${y(t)}" stroke="#e8e8e8"/>`
        + `<text x="${L0 - 4}" y="${y(t) + 3}" text-anchor="end">${fmt(t)}</text>`).join('')
      + `<text x="${L0}" y="${H - 3}">${fmtPacific.format(dates[0]).replace(/,? \d\d:\d\d.*/, '')}</text>`
      + `<text x="${W - 4}" y="${H - 3}" text-anchor="end">${fmtPacific.format(dates[dates.length - 1]).replace(/,? \d\d:\d\d.*/, '')}</text>`
      + `<path d="${d}" fill="none" stroke="#c94f1f" stroke-width="2" stroke-linejoin="round"/>`
      + (mark !== null ? `<line x1="${x(mark)}" x2="${x(mark)}" y1="${T0}" y2="${H - B}" stroke="#999" stroke-dasharray="2 2"/>` : '')
      + `<g class="hover" visibility="hidden"><line y1="${T0}" y2="${H - B}" stroke="#1a1a2e"/><circle r="4" fill="#c94f1f" stroke="white" stroke-width="2"/>`
      + `<text class="tip" y="${T0 + 8}"></text></g>`
      + `<rect x="${L0}" y="0" width="${W - L0}" height="${H}" fill="transparent"/>`;
    const g = svg.querySelector('.hover'), [hl, hc, ht] = g.children;
    svg.onmousemove = ev => {
      const px = ev.clientX - svg.getBoundingClientRect().left;
      const i = Math.max(0, Math.min(dates.length - 1, Math.round((px - L0) / (W - L0 - 4) * (dates.length - 1))));
      g.setAttribute('visibility', 'visible');
      hl.setAttribute('x1', x(i)); hl.setAttribute('x2', x(i));
      hc.setAttribute('cx', x(i)); hc.setAttribute('cy', ok[i] ? y(ys[i]) : -99);
      ht.setAttribute('x', x(i) > W / 2 ? x(i) - 6 : x(i) + 6);
      ht.setAttribute('text-anchor', x(i) > W / 2 ? 'end' : 'start');
      ht.textContent = `${fmtPacific.format(dates[i])}: ${ok[i] ? fmt(ys[i], true) : 'none'}`;
    };
    svg.onmouseleave = () => g.setAttribute('visibility', 'hidden');
  }

  function drawReceptor() {
    $('pl-rec').hidden = false;
    const s = qScale(), q = $('pl-q').value;
    $('pl-rec-title').textContent = `At the receptor (${receptor.lat.toFixed(4)}° N, ${(-receptor.lng).toFixed(4)}° W): `
      + { dil: 'dilution', ta: 'excess TA', dic: 'excess DIC' }[q];
    lineChart($('pl-rec-svg'), R.dates, receptor.series, {
      log: true, mark: view === 'frame' ? frame : null,
      fmt: (f, full) => s === null ? (full ? fmtDil(f) : shortDil(f))
        : `${sig(f * s, 2)}${full ? ' µmol/kg' : ''}`,
    });
  }
  function median(a) {
    const v = a.filter(x => x !== null).sort((x, y) => x - y);
    return v[Math.floor(v.length / 2)];
  }
  function drawNearField() {
    const nf = R.near_field[0], dates = nf.times.map(t => new Date(t)), now = R.dates[frame];
    const k = dates.reduce((b, d, i) => (Math.abs(d - now) < Math.abs(dates[b] - now) ? i : b), 0);
    lineChart($('pl-nf-svg'), dates, nf.dilution.map(v => v ?? NaN), {
      log: true, mark: view === 'frame' ? k : null, fmt: (v, full) => (full ? `1 : ${sig(v)}` : sig(v)),
    });
  }
  function drawBuildUp() {
    const on = R.released_m3.map((v, i) => v * R.on_grid_pct[i] / 100);
    lineChart($('pl-build-svg'), R.dates, on, { fmt: (v, full) => `${sig(v, 2)}${full ? ' m³' : ''}` });
  }

  // ── Show / hide with the tool switch ──
  let loaded = false;
  function show(on) {
    const layersOn = [outfalls, ...(source ? [sourceMark] : []), ...(receptor ? [receptorMark] : []), ...(raster ? [raster] : [])];
    if (on) {
      layersOn.forEach(l => l.addTo(map));
      drawLegend();
      legendCtl.addTo(map);
      L.DomEvent.disableClickPropagation(legend);
      player.style.display = R && view === 'frame' ? 'flex' : 'none';
      currents.setSource('plume');
      if (R) currents.setTime(R.dates[frame]); else if (ph.length) currents.setTime(ph[+$('pl-start').value]);
      if (!loaded) { loaded = true; loadMeta(); }
      if (!source) map.setView([48.075, -123.04], 12);  // Sequim Bay
    } else {
      pause();
      layersOn.forEach(l => l.remove());
      legendCtl.remove();
      player.style.display = 'none';
    }
  }
  ['pl-ta', 'pl-dic'].forEach(id => $(id).addEventListener('input', drawLegend));

  return { show, click };
})();
