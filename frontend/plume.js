// Plume dilution tool (plan.md decisions 10-19; backend/ssm_pt/engine/plume.py).
// Shares the map, basemaps, current arrows and helpers ($, api, sleep, fmtUTC, fmtPacific, currents, addControl)
// with the particle tool in index.html. Up to MAX_SOURCES sources per run, each drawn in its own dye colour.
const Plume = (() => {
  const PCOLOR = { outfall: '#1d3557', receptor: '#7b2cbf' };
  const F_MIN = 1e-5, F_MAX = 1e-1;  // colour scale: dilution 1:100,000 (lightest) to 1:10 (darkest)
  // Single-hue orange ramp, light -> dark (blue would vanish into the ocean basemap)
  const RAMP = ['#fde4d3', '#f9c3a0', '#f49a69', '#eb6834', '#c94f1f', '#9c3a12', '#6b2507'];
  // One dye per source (MAX_SOURCES in plume.py); blended by each source's share where plumes overlap.
  // Ordered so neighbours stay apart for colour-blind readers (dataviz validate_palette.js: CVD ΔE >= 11.8)
  const DYE = ['#e8590c', '#7048e8', '#1baf7a', '#c2255c', '#eda100', '#008300'];
  const MAX_SOURCES = DYE.length;
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
    .sw-sq { display: inline-block; width: 9px; height: 9px; transform: rotate(45deg); border: 1px solid white; box-shadow: 0 0 0 0.5px #999; }
    .src-row { display: flex; align-items: center; gap: 6px; padding: 2px 0; cursor: pointer; }
    .src-row + .src-row { border-top: 1px solid #e8e8e8; }
    .src-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .src-row .rm { font-size: 15px; line-height: 1; color: #777; background: none; border: none; cursor: pointer; }
    .src-row .rm:hover { color: #1a1a2e; }
    .src-num { display: inline-flex; align-items: center; justify-content: center; box-sizing: border-box; flex-shrink: 0;
      width: 16px; height: 16px; border: 2.5px solid; border-radius: 50%; background: white; font-size: 9.5px; font-weight: 700; }
    .leaflet-marker-icon .src-num { width: 20px; height: 20px; font-size: 11px; box-shadow: 0 1px 3px rgba(0,0,0,0.4); }
    button.add-src { width: 100%; margin: 0 0 6px; padding: 3px; font: inherit; font-size: 11.5px; color: #0077b6;
      background: none; border: 1px dashed #0077b6; border-radius: 4px; cursor: pointer; }
    button.add-src:disabled { color: #999; border-color: #ccc; cursor: default; }
    button.info { display: inline-flex; align-items: center; justify-content: center; width: 13px; height: 13px; margin-left: 3px;
      padding: 0; vertical-align: 1px; font: italic 700 9px/1 Georgia, serif; color: #777; background: white;
      border: 1px solid #aaa; border-radius: 50%; cursor: help; }
    button.info:hover, button.info:focus-visible { color: #0077b6; border-color: #0077b6; }
    .info-tip { position: fixed; z-index: 2000; max-width: 260px; padding: 6px 8px; font-size: 11px; line-height: 1.4; color: white;
      background: #1a1a2e; border-radius: 4px; box-shadow: 0 2px 8px rgba(0,0,0,0.3); pointer-events: none; }
    .info-tip[hidden] { display: none; }`;
  document.head.appendChild(style);

  const P = $('mode-plume');
  P.innerHTML = `
  <details class="section" open>
    <summary>1 · Sources</summary>
    <div class="section-body">
      <div id="pl-srcs" class="release-box"></div>
      <button id="pl-add" type="button" class="add-src" hidden></button>
      <div class="hint" id="pl-src-note"></div>
      <div id="pl-src-form">
      <div class="field"><label for="pl-name">Name</label><input id="pl-name" type="text" maxlength="120" value="Source"></div>
      <div class="field"><label for="pl-flow">Flow (m³/s)</label><input id="pl-flow" type="number" min="0.0001" max="100" step="any" value="0.00164"></div>
      <div class="hint" id="pl-flow-hint"></div>
      <div class="field"><label for="pl-nf">Near field</label>
        <select id="pl-nf"><option value="fixed" selected>Fixed values</option><option value="plumes">PLUMES, every hour (Sequim Bay)</option></select>
      </div>
      <div id="pl-nf-plumes" hidden>
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
      <div id="pl-nf-fixed">
        <div class="field"><label for="pl-s0">Near-field dilution</label><input id="pl-s0" type="number" min="1" max="10000" step="0.5" value="8"></div>
        <div class="field"><label for="pl-diam">Plume diameter (m)</label><input id="pl-diam" type="number" min="0.1" max="1000" step="0.1" value="1.6"></div>
        <div class="hint">From your own near-field run (PLUMES). Defaults are the Admiralty Inlet TD1 case in Savoie et al. The near-field dilution caps how concentrated the plume can get; the diameter sets the release spot.</div>
      </div>
      <div class="field"><label for="pl-ta">Excess TA (µmol/kg)</label><input id="pl-ta" type="number" min="0" max="100000" step="100" value="5500"></div>
      <div class="field"><label for="pl-dic">Excess DIC (µmol/kg)</label><input id="pl-dic" type="number" min="0" max="100000" step="100" value="2300"></div>
      <div class="hint">Effluent minus ambient. Defaults: the TD1 effluent (TA ~7,600, DIC 4,312) minus a typical Salish Sea ambient (~2,100 and ~2,000). Replace with measured values.</div>
      </div>
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
      <div class="hint">Each source discharges continuously from Start to End.</div>
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
    <div class="field" id="pl-by-f" hidden><label for="pl-by">Colour by</label>
      <select id="pl-by"><option value="dye" selected>Source (dye colours)</option><option value="total">Total only</option></select>
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
  let ph = [], meta = null, R = null, view = null, frame = 0, timer = null, receptor = null;
  let sources = [], sel = -1, adding = false;  // each source: { lon, lat, p (outfall properties or null), form }
  // frame key -> { tot, by }: effluent fraction summed and per source (Float32Arrays, row-major from the south)
  const frames = new Map();
  const outfalls = L.layerGroup(), srcLayer = L.layerGroup();
  const receptorMark = L.circleMarker([0, 0], { radius: 5, weight: 2, color: 'white', fillColor: PCOLOR.receptor, fillOpacity: 1, interactive: false });
  let raster = null;

  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
  const sig = (x, n = 3) => Number(x.toPrecision(n)).toLocaleString('en-US');
  const fmtDil = f => f > 0 ? `1 : ${sig(1 / f)}` : '–';
  const shortDil = f => { const n = 1 / f; return `1:${n >= 1e6 ? `${sig(n / 1e6)}M` : n >= 1e4 ? `${sig(n / 1e3)}k` : sig(n)}`; };
  const setStatus = (msg, error = false) => { $('pl-status').textContent = msg; $('pl-status').classList.toggle('error', error); };

  // ── Info bubbles: an (i) after each setting's label; hover, focus or tap it for more ──
  const TIPS = {
    'pl-name': 'A label for this source in the list, legend, hover readout and charts. It does not change the run.',
    'pl-flow': 'Effluent discharge, all ports together (1 m³/s = 3,600 m³/h ≈ 22.8 MGD). It sets how much effluent each '
      + 'particle carries, so the map\'s dilution scales with it; with PLUMES it also sets the jet speed. The default, '
      + '0.00164 m³/s (5.9 m³/h), is Ebb Carbon\'s Macoma discharge, a placeholder.',
    'pl-nf': 'How the effluent mixes in its first metres to tens of metres, far smaller than a map cell. "PLUMES, every hour" '
      + 'runs the UM3 jet model on each hour\'s modelled current, temperature and salinity at the source (Sequim Bay only). '
      + '"Fixed values" takes a dilution and width from your own near-field run, the same every hour; use it outside '
      + 'Sequim Bay. Either way, the near-field dilution caps the map and its width sets where particles start.',
    'pl-ports': 'Number of openings on the diffuser. The flow is shared equally among them, so more ports make more, '
      + 'smaller jets. With the spacing, it sets the diffuser\'s length.',
    'pl-pd': 'Inside diameter of each port. Smaller ports make faster jets (exit speed below), which draw in more '
      + 'seawater. 12.7 mm is ½ inch.',
    'pl-sp': 'Distance between neighbouring ports. Close jets merge sooner, which slows their mixing. With the number '
      + 'of ports, it sets how wide the plume is where the map takes over.',
    'pl-va': 'Jet angle above horizontal: 0° horizontal, 90° straight up, negative points down. Angled jets travel '
      + 'farther through the water before they rise to the surface or level off, so they have longer to mix.',
    'pl-brg': 'Compass direction the jets point (0° north, 90° east). Blank points them with each hour\'s current at the '
      + 'port. Enter the real diffuser\'s bearing once known: the tide then runs across or against the jets on some hours.',
    'pl-pdep': 'Depth of the ports below mean sea level. They stay put as the tide rises and falls; the water above them '
      + 'changes. Must be shallower than the model\'s seabed at the source (an outfall\'s note shows that depth). The '
      + 'current, temperature and salinity the jets meet are read at this depth.',
    'pl-es': 'Effluent salinity (about 30–31 for Sequim Bay seawater, 0 for fresh water). Blank uses the water at the '
      + 'port, as for a seawater intake. Fresher, lighter effluent rises faster.',
    'pl-et': 'Effluent temperature. Blank uses the water at the port. Warmer effluent is lighter and rises faster; '
      + 'with salinity it sets how buoyant the plume is.',
    'pl-s0': 'Dilution where the near field ends (1 part effluent in N parts seawater), from your own PLUMES run. The '
      + 'map never shows this source less diluted than this. Default 8: the Admiralty Inlet TD1 case in Savoie et al.',
    'pl-diam': 'Plume width where the near field ends, from the same run. Particles start spread across it. '
      + 'Default 1.6 m: the TD1 case.',
    'pl-ta': 'Effluent total alkalinity minus the ambient seawater\'s. The map\'s excess TA is this divided by the '
      + 'dilution. Replace the default with measured values.',
    'pl-dic': 'Effluent dissolved inorganic carbon minus the ambient seawater\'s. Like TA, it is divided by the dilution.',
    'pl-start': 'First hour the sources discharge (UTC; Pacific time below). Before a run, the arrows show the '
      + 'depth-averaged current at this hour.',
    'pl-end': 'Last hour of the run. Every source discharges at a steady rate the whole time. A longer run shows '
      + 'whether the area flushes the effluent as fast as it arrives: the build-up chart levels off.',
    'pl-dtout': 'How often a map is saved, for playback, receptor charts and the "over the run" maps. The "lowest '
      + 'dilution over the run" only sees saved times, so long intervals can miss short tidal peaks. The physics is '
      + 'unchanged (that is the time step).',
    'pl-n': 'Particles released over the run, shared equally among the sources. Dilution comes from counting them in '
      + 'each cell, so more particles give smoother maps far from the source, where few arrive. Run time grows only '
      + 'slowly with the count.',
    'pl-dt': 'How far particles move between updates. Shorter steps follow fast, turning tidal currents more closely, '
      + 'but run time scales with the number of steps.',
    'pl-kh': 'Random spreading added to the model currents, for eddies smaller than the model resolves. Larger values '
      + 'spread and dilute the plume faster. Ocean dye studies (Okubo 1971) suggest about 0.1 m²/s for patches a few '
      + 'hundred metres across and about 1 m²/s for 1–2 km. Not calibrated here.',
    'pl-col': 'How deep the effluent is spread when particle counts become dilution. "Whole water column": surface to '
      + 'seabed, the most dilute case. "Surface layer": the top few metres only, as for a light plume over stratified '
      + 'water; dilution is then lower by the ratio of water depth to layer thickness.',
    'pl-mix': 'Thickness of the surface layer. Where the water is shallower, the whole depth is used. With PLUMES, the '
      + 'result\'s near-field depth is a guide.',
    'pl-cell': 'Size of the square cells dilution is averaged over. Smaller cells show sharper, less diluted peaks '
      + 'near a source, but each holds fewer particles, so the map gets noisier (raise the particle count).',
    'pl-span': 'How far the map reaches around each source: half this distance in every direction (a square this wide '
      + 'around a lone source; with several, the map stretches to cover them all, up to 200 km across). Effluent that '
      + 'leaves the map is no longer counted ("Still on the map"), so widen it for long runs.',
    'pl-q': 'What the map, legend and receptor chart show. Dilution: 1 part effluent in N parts seawater. Excess TA or '
      + 'DIC: each source\'s excess divided by its dilution, added over sources. pH and saturation state are not '
      + 'computed yet.',
    'pl-by': '"Source" tints each cell with the sources\' dye colours, mixed by each one\'s share of the effluent there '
      + '(hover for the shares). "Total only" shows the summed field on one colour scale.',
    'pl-view': '"Each saved time" plays the run. "Lowest dilution over the run": each cell at its most concentrated '
      + 'saved time, with each source\'s share at that moment. "Mean over the run": the time average, closer to a '
      + 'long-term exposure.',
  };
  const tip = document.createElement('div');
  Object.assign(tip, { className: 'info-tip', id: 'pl-tip', hidden: true });
  tip.setAttribute('role', 'tooltip');
  document.body.appendChild(tip);
  let tipFor = null;
  function showTip(b) {
    hideTip();
    tipFor = b;
    tip.textContent = TIPS[b.dataset.tip];
    tip.hidden = false;
    b.setAttribute('aria-describedby', tip.id);
    const r = b.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;  // below the (i), or above near the bottom
    tip.style.left = `${Math.max(8, Math.min(r.left - 12, innerWidth - w - 8))}px`;
    tip.style.top = `${r.bottom + 6 + h > innerHeight - 8 ? r.top - 6 - h : r.bottom + 6}px`;
  }
  function hideTip() {
    tipFor?.removeAttribute('aria-describedby');
    tipFor = null;
    tip.hidden = true;
  }
  for (const id of Object.keys(TIPS)) {
    const label = P.querySelector(`label[for="${id}"]`);
    label.insertAdjacentHTML('beforeend', `&nbsp;<button type="button" class="info" data-tip="${id}" aria-label="About ${esc(label.textContent)}">i</button>`);
    const b = label.lastElementChild;
    b.addEventListener('mouseenter', () => showTip(b));
    b.addEventListener('mouseleave', hideTip);
    b.addEventListener('focus', () => showTip(b));
    b.addEventListener('blur', hideTip);
    b.addEventListener('click', e => { e.preventDefault(); showTip(b); });  // taps; and keeps the label from focusing its input
  }
  P.closest('.panel')?.addEventListener('scroll', hideTip);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hideTip(); });

  // ── Colour ──
  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const RGB = RAMP.map(hex), DYE_RGB = DYE.map(hex), GREY = [100, 100, 100];
  const scaleT = f => Math.min(1, (Math.log10(f) - Math.log10(F_MIN)) / (Math.log10(F_MAX) - Math.log10(F_MIN)));
  const alpha = t => Math.round(255 * (0.55 + 0.4 * t));  // the faintest plume stays see-through
  function colour(f) {  // -> [r, g, b, a] or null below the scale
    if (!(f >= F_MIN)) return null;
    const t = scaleT(f);
    const x = t * (RGB.length - 1), i = Math.min(RGB.length - 2, Math.floor(x)), w = x - i;
    const c = RGB[i].map((v, k) => Math.round(v + w * (RGB[i + 1][k] - v)));
    return [...c, alpha(t)];
  }
  function shade(base, f) {  // a dye colour, pale at high dilution and dark at low, like colour()
    if (!(f >= F_MIN)) return null;
    const t = scaleT(f);
    const c = t < 0.6 ? base.map(v => v + (255 - v) * 0.75 * (1 - t / 0.6)) : base.map(v => v * (1 - 0.5 * (t - 0.6) / 0.4));
    return [...c.map(Math.round), alpha(t)];
  }
  // The sources' dyes mixed by their shares of the cell's effluent
  const mixDyes = shares => [0, 1, 2].map(j => shares.reduce((a, w, i) => a + w * DYE_RGB[i][j], 0));
  const numStyle = (i, on) => `border-color:${DYE[i]};` + (on ? `background:${DYE[i]};color:white` : `color:${DYE[i]}`);

  // ── Legend: colour bar for the shown quantity, plus the map symbols ──
  const legend = L.DomUtil.create('div', 'card legend colorbar');
  const legendCtl = L.control({ position: 'bottomleft' });
  legendCtl.onAdd = () => legend;
  function drawLegend() {
    const q = $('pl-q').value, dye = byDye();
    const scale = q === 'dil' ? null : R ? qRef() : +$(q === 'ta' ? 'pl-ta' : 'pl-dic').value;
    const fs = [F_MIN, 1e-4, 1e-3, 1e-2, F_MAX];
    const tick = f => scale === null ? (f === F_MIN ? '1:100k' : f === 1e-4 ? '1:10k' : `1:${sig(1 / f)}`) : sig(f * scale, 2);
    legend.innerHTML = `<h4>${{ dil: 'Dilution (1 : N)', ta: 'Excess TA (µmol/kg)', dic: 'Excess DIC (µmol/kg)' }[q]}</h4>
      <canvas width="180" height="1"></canvas><div class="ticks">${fs.map(f => `<span>${tick(f)}</span>`).join('')}</div>
      <div class="hint" style="margin:3px 0 4px">${scale === null ? 'Darker = less diluted' : 'Darker = more effluent'}; clear below 1:100,000${dye ? '.<br>Colour = which source; mixed where plumes overlap' : ''}</div>
      ${dye ? R.body.sources.map((s, i) => `<div class="legend-item"><span class="legend-swatch"><span class="sw-dot" style="background:${DYE[i]};width:10px;height:10px"></span></span>${esc(s.name)}</div>`).join('') : ''}
      <div class="legend-item"><span class="legend-swatch"><span class="src-num" style="${numStyle(0, false)}">1</span></span>Source (numbered)</div>
      <div class="legend-item"><span class="legend-swatch"><span class="sw-sq" style="background:${PCOLOR.outfall}"></span></span>Outfall or candidate site</div>
      <div class="legend-item"><span class="legend-swatch"><span class="sw-dot" style="background:${PCOLOR.receptor};width:9px;height:9px"></span></span>Receptor point</div>
      <div class="legend-item"><span class="legend-swatch"><svg width="14" height="10"><path d="M1 5H12M8.5 2L12 5L8.5 8" stroke="#1d3557" stroke-width="1.3" fill="none"/></svg></span>Depth-averaged current</div>`;
    const c = legend.querySelector('canvas'), ctx = c.getContext('2d');
    for (let x = 0; x < 180; x++) {
      const f = 10 ** (Math.log10(F_MIN) + (x + 0.5) / 180 * (Math.log10(F_MAX) - Math.log10(F_MIN)));
      const [r, g, b, a] = dye ? shade(GREY, f) : colour(f);
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
    $('pl-run').disabled = !sources.length;
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
        .on('click', e => { L.DomEvent.stopPropagation(e); place(lon, lat, p); })
        .addTo(outfalls);
    }
  }

  // ── Sources: each keeps its own copy of the section-1 form, which edits the selected one ──
  const SRC_FIELDS = ['pl-name', 'pl-flow', 'pl-nf', 'pl-ports', 'pl-pd', 'pl-sp', 'pl-va', 'pl-brg', 'pl-pdep',
    'pl-es', 'pl-et', 'pl-s0', 'pl-diam', 'pl-ta', 'pl-dic'];
  const readForm = () => Object.fromEntries(SRC_FIELDS.map(id => [id, $(id).value]));
  $('pl-src-form').addEventListener('input', () => { if (sel >= 0) { sources[sel].form = readForm(); renderSources(); } });
  $('pl-src-form').addEventListener('change', () => { if (sel >= 0) sources[sel].form = readForm(); });

  function place(lon, lat, p = null) {
    if (adding || !sources.length) {  // a new source starts from the settings on screen
      sources.push({ form: { ...readForm(), 'pl-name': `Source ${sources.length + 1}` } });
      sel = sources.length - 1;
      adding = false;
    }
    const s = sources[sel];
    Object.assign(s, { lon: +lon.toFixed(5), lat: +lat.toFixed(5), p });
    if (p) {
      s.form['pl-name'] = p.name.slice(0, 120);
      if (p.flow_m3s) s.form['pl-flow'] = String(p.flow_m3s);
    }
    select(sel);
  }
  function select(i) {
    sel = i;
    if (i >= 0) SRC_FIELDS.forEach(id => { $(id).value = sources[i].form[id]; });
    renderSources();
    updateFlowHint();
  }
  function removeSource(i) {
    sources.splice(i, 1);
    adding = adding && sources.length > 0;
    select(i < sel ? sel - 1 : Math.min(sel, sources.length - 1));
  }
  function renderSources() {
    srcLayer.clearLayers();
    sources.forEach((s, i) => L.marker([s.lat, s.lon], {
      icon: L.divIcon({ className: '', iconSize: [20, 20], html: `<span class="src-num" style="${numStyle(i, i === sel)}">${i + 1}</span>` }),
      interactive: false, keyboard: false, zIndexOffset: i === sel ? 1000 : 0,
    }).addTo(srcLayer));
    $('pl-srcs').classList.toggle('set', sources.length > 0);
    $('pl-srcs').innerHTML = sources.length ? sources.map((s, i) => `<div class="src-row" data-i="${i}">
        <span class="src-num" style="${numStyle(i, i === sel)}">${i + 1}</span>
        <span class="src-label"><b>${esc(s.form['pl-name'])}</b> <span class="coords">${s.lat.toFixed(4)}° N, ${(-s.lon).toFixed(4)}° W</span></span>
        <button class="rm" data-rm="${i}" title="Remove">×</button></div>`).join('')
      : 'Click an outfall (◆) or the water to place a source.';
    const s = sources[sel], p = s?.p;
    $('pl-src-note').textContent = adding ? `Click an outfall (◆) or the water to place source ${sources.length + 1}.`
      : !s ? '' : (p ? `${p.kind}. Snapped ${p.snap_m} m to the model mesh (${p.depth_m} m deep).` + (p.flow_m3s ? '' : ' No flow on record: enter one.') + ' ' : '')
        + `The settings below are for source ${sel + 1}${sources.length > 1 ? ' (click a row to pick another)' : ''}; click the map to move it.`;
    $('pl-add').hidden = !sources.length;
    $('pl-add').disabled = adding || sources.length >= MAX_SOURCES;
    $('pl-add').textContent = sources.length >= MAX_SOURCES ? `Up to ${MAX_SOURCES} sources` : '+ Add another source';
    $('pl-run').disabled = !sources.length || !ph.length;
  }
  $('pl-srcs').addEventListener('click', e => {
    const rm = e.target.closest('[data-rm]'), row = e.target.closest('[data-i]');
    if (rm) removeSource(+rm.dataset.rm); else if (row) select(+row.dataset.i);
  });
  $('pl-add').addEventListener('click', () => { adding = true; renderSources(); });
  renderSources();

  function updateFlowHint() {
    const q = +$('pl-flow').value, s = sources[sel], p = s?.p;
    $('pl-flow-hint').textContent = `${sig(q * 3600)} m³/h, ${sig(q / MGD)} MGD.` + (p?.flow_mgd ? ` Listed design flow ${sig(p.flow_mgd)} MGD.`
      : s && !p?.flow_m3s ? ' Placeholder (Ebb\'s Macoma discharge until set): enter the outfall\'s flow.' : '');
    updateNearField();
  }
  $('pl-flow').addEventListener('input', updateFlowHint);

  const plumes = () => $('pl-nf').value === 'plumes';
  const optional = v => v === '' ? null : +v;
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

  async function setReceptor(latlng) {
    receptor = latlng;
    receptorMark.setLatLng(latlng).addTo(map);
    try {
      const r = await api('GET', `plumes/${R.id}/receptor?lon=${latlng.lng}&lat=${latlng.lat}`);
      receptor.series = r.fraction;
      receptor.by = r.by_source ?? [r.fraction];
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
    place(e.latlng.lng, e.latlng.lat);
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
      sources: sources.map(({ lon, lat, form: f }, i) => ({
        name: f['pl-name'] || `Source ${i + 1}`, lon, lat, flow_m3s: +f['pl-flow'],
        near_field_dilution: +f['pl-s0'], plume_diameter_m: +f['pl-diam'],
        excess_ta: +f['pl-ta'], excess_dic: +f['pl-dic'],
        diffuser: f['pl-nf'] === 'plumes' ? {
          n_ports: +f['pl-ports'], port_diameter_m: +f['pl-pd'] / 1000, port_spacing_m: +f['pl-sp'],
          vertical_angle_deg: +f['pl-va'], bearing_deg: optional(f['pl-brg']), port_depth_m: +f['pl-pdep'],
          effluent_salinity: optional(f['pl-es']), effluent_temperature_c: optional(f['pl-et']),
        } : null,
      })),
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
        setStatus(`${s.status === 'queued' ? 'Waiting for a worker' : (body.sources.some(x => x.diffuser) ? `PLUMES for each hour, then ` : '')
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
      $('pl-run').disabled = !sources.length;
    }
  });

  // ── Result ──
  async function showResult(id, m, body) {
    R = { ...m, id, body, dates: m.times.map(t => new Date(t)) };
    frames.clear();
    receptor = null;
    receptorMark.remove();
    $('pl-rec').hidden = true;
    const srcs = body.sources, nfs = R.near_field ?? srcs.map(() => null), multi = srcs.length > 1;
    const range = (a, n = 2) => { const v = a.filter(x => x !== null); return `${sig(Math.min(...v), n)}–${sig(Math.max(...v), n)}`; };
    $('pl-nf-res').hidden = !nfs.some(Boolean);
    $('pl-nf-sum').innerHTML = nfs.map((nf, i) => nf && (multi ? `<b>${esc(srcs[i].name)}</b>: ` : '') + esc(
      `Median 1 : ${sig(median(nf.dilution))}, range 1 : ${range(nf.dilution, 3)}. Where the near field ends the plume `
        + `is ${range(nf.trap_depth_m)} m below the surface and ${range(nf.width_m)} m wide; current at the port ${range(nf.current_m_s)} m/s.`
        + (nf.failed.length ? ` Failed for ${nf.failed.length} of ${nf.times.length} hours (bridged from the hours around them), first: ${nf.failed[0]}.` : '')
        + (nf.warnings.length ? ` plumes2 warned: ${nf.warnings.join(' · ')}` : ''))).filter(Boolean).join('<br>');
    $('pl-kind').textContent = srcs.map((s, i) => `${s.name}: ${sig(s.flow_m3s)} m³/s, `
      + (nfs[i] ? `near field from PLUMES each hour (median 1 : ${sig(median(nfs[i].dilution))})` : `near-field dilution ${s.near_field_dilution}`)).join('; ')
      + `; ${body.mixing_depth_m ? `top ${body.mixing_depth_m} m` : 'whole water column'}, ${R.cell_m} m cells, ${body.dt_s} s steps.`;
    $('pl-by-f').hidden = !multi;
    $('pl-s-min').textContent = R.min_dilution ? `1 : ${sig(R.min_dilution)}` : '–';
    $('pl-s-rel').textContent = sig(R.released_m3[R.released_m3.length - 1]);
    $('pl-s-on').textContent = `${Math.round(R.on_grid_pct[R.on_grid_pct.length - 1])}%`;
    $('pl-s-n').textContent = sig(R.n_particles);
    $('pl-results').hidden = false;
    slider.max = R.times.length - 1;
    drawLegend();
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
    const grid = vals => { const a = new Float32Array(R.rows * R.cols); d.idx.forEach((k, i) => { a[k] = vals[i]; }); return a; };
    const tot = grid(d.val), fr = { tot, by: d.by_source ? d.by_source.map(grid) : [tot] };
    frames.set(key, fr);
    return fr;
  }

  let shown = null;  // the frame on the map
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
    if (R.near_field?.some(Boolean)) drawNearField();
    if (view === 'frame' && timer && k + 1 < R.times.length) getFrame(k + 1);  // prefetch while playing
  }

  // Excess TA or DIC adds up over sources with their own effluent values; the colour scale shows it as a fraction
  // of the largest source's value (the legend ticks multiply by that). Dilution: the summed fraction.
  function chem() { return { ta: 'excess_ta', dic: 'excess_dic' }[$('pl-q').value]; }
  function qRef() { const c = chem(); return c ? Math.max(...R.body.sources.map(s => s[c])) : null; }
  function byDye() { return !!R && R.body.sources.length > 1 && $('pl-by').value === 'dye'; }
  const chemAt = (fr, k, c) => R.body.sources.reduce((a, s, i) => a + fr.by[i][k] * s[c], 0);

  function drawRaster(fr) {
    const c = document.createElement('canvas');
    c.width = R.cols;
    c.height = R.rows;
    const ctx = c.getContext('2d'), img = ctx.createImageData(R.cols, R.rows);
    const q = chem(), ref = qRef(), dye = byDye();
    for (let k = 0; k < fr.tot.length; k++) {
      const f = fr.tot[k];
      if (!(f > 0)) continue;
      const v = q ? chemAt(fr, k, q) / ref : f;
      const rgba = dye ? shade(mixDyes(fr.by.map(b => b[k] / f)), v) : colour(v);
      if (!rgba) continue;
      const r = Math.floor(k / R.cols), col = k % R.cols, o = 4 * ((R.rows - 1 - r) * R.cols + col);  // row 0 is the south edge
      img.data.set(rgba, o);
    }
    ctx.putImageData(img, 0, 0);
    if (raster) raster.setUrl(c.toDataURL()).setBounds(L.latLngBounds(R.bounds));
    else raster = L.imageOverlay(c.toDataURL(), R.bounds, { className: 'plume-raster', interactive: false }).addTo(map);
  }

  map.on('mousemove', e => {
    if (mode !== 'plume' || !R || !shown) return;
    const [[s, w], [n, ea]] = R.bounds, { lat, lng } = e.latlng;
    if (lat < s || lat > n || lng < w || lng > ea) return;
    const r = Math.floor((lat - s) / (n - s) * R.rows), c = Math.floor((lng - w) / (ea - w) * R.cols);
    const k = r * R.cols + c, f = shown.tot[k], srcs = R.body.sources;
    const shares = srcs.length > 1 ? ' · ' + srcs.map((x, i) => `${x.name} ${Math.round(100 * shown.by[i][k] / f)}%`).join(', ') : '';
    $('pl-hover').textContent = `Cursor: ${f > 0 ? `dilution ${fmtDil(f)} · excess TA ${sig(chemAt(shown, k, 'excess_ta'), 2)} µmol/kg${shares}` : 'no effluent'}`
      + (view === 'frame' ? '' : ` (${view} over the run)`) + '. Shift-click to add a receptor point.';
  });

  $('pl-q').addEventListener('change', () => { drawLegend(); if (R && shown) drawRaster(shown); if (receptor?.series) drawReceptor(); });
  $('pl-by').addEventListener('change', () => { drawLegend(); if (R && shown) drawRaster(shown); });
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

  // ── Charts: a line over the run's times (plus thinner per-source lines), crosshair readout on hover ──
  function lineChart(svg, dates, ys, { log = false, fmt, mark = null, stroke = '#c94f1f', lines = [] }) {
    const W = svg.clientWidth || 280, H = 120, L0 = 44, B = 16, T0 = 6;
    const okv = v => (log ? v > 0 : Number.isFinite(v));
    const ok = ys.map(okv);
    const vals = [ys, ...lines.map(l => l.ys)].flat().filter(okv);
    if (!vals.length) { svg.innerHTML = `<text x="${L0}" y="${H / 2}">No effluent here during the run</text>`; return; }
    const tf = log ? Math.log10 : y => y;
    let lo = Math.min(...vals.map(tf)), hi = Math.max(...vals.map(tf));
    if (log) { hi = Math.ceil(hi); lo = Math.max(Math.floor(lo), hi - 7); } else { lo = Math.min(0, lo); }
    if (hi === lo) hi = lo + 1;
    const x = i => L0 + (W - L0 - 4) * (dates.length > 1 ? i / (dates.length - 1) : 0);
    const y = v => T0 + (H - T0 - B) * (1 - (tf(v) - lo) / (hi - lo));
    const ticks = log ? Array.from({ length: hi - lo + 1 }, (_, k) => 10 ** (lo + k)) : [lo, (lo + hi) / 2, hi];
    const path = vs => {
      let d = '', pen = false;
      vs.forEach((v, i) => { if (okv(v) && tf(v) >= lo) { d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true; } else pen = false; });
      return d;
    };
    svg.innerHTML = ticks.map(t => `<line x1="${L0}" x2="${W - 4}" y1="${y(t)}" y2="${y(t)}" stroke="#e8e8e8"/>`
        + `<text x="${L0 - 4}" y="${y(t) + 3}" text-anchor="end">${fmt(t)}</text>`).join('')
      + `<text x="${L0}" y="${H - 3}">${fmtPacific.format(dates[0]).replace(/,? \d\d:\d\d.*/, '')}</text>`
      + `<text x="${W - 4}" y="${H - 3}" text-anchor="end">${fmtPacific.format(dates[dates.length - 1]).replace(/,? \d\d:\d\d.*/, '')}</text>`
      + lines.map(l => `<path d="${path(l.ys)}" fill="none" stroke="${l.stroke}" stroke-width="1.3" stroke-linejoin="round"/>`).join('')
      + `<path d="${path(ys)}" fill="none" stroke="${stroke}" stroke-width="2" stroke-linejoin="round"/>`
      + (mark !== null ? `<line x1="${x(mark)}" x2="${x(mark)}" y1="${T0}" y2="${H - B}" stroke="#999" stroke-dasharray="2 2"/>` : '')
      + `<g class="hover" visibility="hidden"><line y1="${T0}" y2="${H - B}" stroke="#1a1a2e"/><circle r="4" fill="${stroke}" stroke="white" stroke-width="2"/>`
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
    const c = chem(), srcs = R.body.sources, multi = srcs.length > 1;
    const each = receptor.by.map((b, i) => (c ? b.map(f => f * srcs[i][c]) : b));  // per source, in the shown quantity
    const total = c ? R.dates.map((_, t) => each.reduce((a, e) => a + e[t], 0)) : receptor.series;
    $('pl-rec-title').textContent = `At the receptor (${receptor.lat.toFixed(4)}° N, ${(-receptor.lng).toFixed(4)}° W): `
      + { dil: 'dilution', ta: 'excess TA', dic: 'excess DIC' }[$('pl-q').value] + (multi ? ' (total in black, each source in its colour)' : '');
    lineChart($('pl-rec-svg'), R.dates, total, {
      log: true, mark: view === 'frame' ? frame : null, stroke: multi ? '#1a1a2e' : undefined,
      lines: multi ? each.map((ys, i) => ({ ys, stroke: DYE[i] })) : [],
      fmt: (v, full) => c ? `${sig(v, 2)}${full ? ' µmol/kg' : ''}` : (full ? fmtDil(v) : shortDil(v)),
    });
  }
  function median(a) {
    const v = a.filter(x => x !== null).sort((x, y) => x - y);
    return v[Math.floor(v.length / 2)];
  }
  function drawNearField() {  // one line per source with a coupled near field, in its dye colour when several
    const [first, ...rest] = R.near_field.map((nf, i) => nf && { nf, i }).filter(Boolean);
    const dates = first.nf.times.map(t => new Date(t)), now = R.dates[frame], multi = R.body.sources.length > 1;
    const k = dates.reduce((b, d, i) => (Math.abs(d - now) < Math.abs(dates[b] - now) ? i : b), 0);
    const ys = n => n.nf.dilution.map(v => v ?? NaN);
    lineChart($('pl-nf-svg'), dates, ys(first), {
      log: true, mark: view === 'frame' ? k : null, fmt: (v, full) => (full ? `1 : ${sig(v)}` : sig(v)),
      stroke: multi ? DYE[first.i] : undefined, lines: rest.map(n => ({ ys: ys(n), stroke: DYE[n.i] })),
    });
  }
  function drawBuildUp() {
    const on = R.released_m3.map((v, i) => v * R.on_grid_pct[i] / 100);
    lineChart($('pl-build-svg'), R.dates, on, { fmt: (v, full) => `${sig(v, 2)}${full ? ' m³' : ''}` });
  }

  // ── Show / hide with the tool switch ──
  let loaded = false;
  function show(on) {
    const layersOn = [outfalls, srcLayer, ...(receptor ? [receptorMark] : []), ...(raster ? [raster] : [])];
    if (on) {
      layersOn.forEach(l => l.addTo(map));
      drawLegend();
      legendCtl.addTo(map);
      L.DomEvent.disableClickPropagation(legend);
      player.style.display = R && view === 'frame' ? 'flex' : 'none';
      currents.setSource('plume');
      if (R) currents.setTime(R.dates[frame]); else if (ph.length) currents.setTime(ph[+$('pl-start').value]);
      if (!loaded) { loaded = true; loadMeta(); }
      if (!sources.length) map.setView([48.075, -123.04], 12);  // Sequim Bay
    } else {
      pause();
      hideTip();
      layersOn.forEach(l => l.remove());
      legendCtl.remove();
      player.style.display = 'none';
    }
  }
  ['pl-ta', 'pl-dic'].forEach(id => $(id).addEventListener('input', drawLegend));

  return { show, click };
})();
