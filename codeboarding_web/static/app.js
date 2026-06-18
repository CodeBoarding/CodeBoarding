// codeboarding_web/static/app.js
const STYLE = [
  { selector: 'node', style: { 'label': 'data(label)', 'color': '#E6E6E6', 'background-color': '#2196F3',
      'text-valign': 'center', 'text-halign': 'center', 'font-size': 10, 'width': 'label', 'padding': '8px' } },
  { selector: 'node.changed', style: { 'background-color': '#4CAF50', 'border-color': '#4CAF50', 'border-width': 3 } },
  { selector: 'edge', style: { 'width': 1.5, 'line-color': '#555', 'target-arrow-color': '#555',
      'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'label': 'data(label)', 'font-size': 7, 'color': '#888' } },
];

const cy = cytoscape({ container: document.getElementById('cy'), elements: [], style: STYLE });
let hasLaidOut = false;

function logLine(text) {
  const el = document.createElement('div');
  el.className = 'log-line';
  el.textContent = text;
  const log = document.getElementById('log');
  log.prepend(el);
}

function setPhase(phase) {
  const el = document.getElementById('phase');
  el.textContent = phase;
  el.className = 'phase ' + phase;
}

// In-place apply: preserve pan/zoom, add/remove/update, highlight new/changed nodes.
function applyElements(elements) {
  const pan = cy.pan();
  const zoom = cy.zoom();
  const incoming = new Map(elements.map((e) => [e.data.id, e]));
  const existing = new Set(cy.elements().map((e) => e.id()));

  cy.elements().forEach((e) => { if (!incoming.has(e.id())) e.remove(); });

  const added = [];
  incoming.forEach((e, id) => {
    if (existing.has(id)) {
      cy.getElementById(id).data(e.data);
    } else {
      const el = cy.add(e);
      if (el.isNode()) added.push(el);
    }
  });

  if (!hasLaidOut) {
    cy.layout({ name: 'dagre', rankDir: 'LR' }).run();
    cy.fit(undefined, 30);
    hasLaidOut = true;
  } else if (added.length) {
    // Lay out only new nodes; lock existing positions; keep viewport.
    const locked = cy.nodes().difference(cy.collection(added));
    locked.lock();
    cy.layout({ name: 'dagre', rankDir: 'LR', fit: false }).run();
    locked.unlock();
    cy.pan(pan);
    cy.zoom(zoom);
  }

  added.forEach((n) => {
    n.addClass('changed');
    setTimeout(() => n.removeClass('changed'), 1500);
  });
}

async function loadDiagram() {
  const res = await fetch('/api/diagram.json');
  if (res.ok) applyElements((await res.json()).elements);
}

async function refreshStatus() {
  const s = await (await fetch('/api/status')).json();
  document.getElementById('project').textContent = s.project;
  setPhase(s.phase);
  if (s.has_baseline) loadDiagram();
  document.getElementById('watch').checked = s.watch_enabled;
}

function connectEvents() {
  const src = new EventSource('/api/events');
  src.addEventListener('step_start', (e) => logLine('▶ ' + JSON.parse(e.data).step));
  src.addEventListener('step_end', (e) => logLine('✓ ' + JSON.parse(e.data).step));
  src.addEventListener('phase_change', (e) => logLine('— ' + JSON.parse(e.data).step));
  src.addEventListener('diagram_delta', (e) => applyElements(JSON.parse(e.data).elements));
  src.addEventListener('run_end', () => { setPhase('done'); loadDiagram(); });
  src.addEventListener('run_error', (e) => { setPhase('error'); logLine('ERROR: ' + JSON.parse(e.data).error); });
  src.addEventListener('run_notice', (e) => logLine('ℹ ' + JSON.parse(e.data).message));
  src.addEventListener('watch_triggered', (e) => {
    const d = JSON.parse(e.data);
    if (d.status === 'no_baseline') {
      logLine('no baseline — run a full analysis first');
    } else {
      logLine('change detected → re-analyzing');
    }
  });
}

document.getElementById('watch').addEventListener('change', async (e) => {
  const res = await fetch('/api/watch', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: e.target.checked }),
  });
  if (!res.ok) {
    logLine('watch toggle failed');
    e.target.checked = !e.target.checked;
    return;
  }
  const d = await res.json();
  logLine('watch ' + (d.watch_enabled ? 'enabled' : 'disabled'));
});

document.getElementById('run').addEventListener('click', async () => {
  const scope = document.getElementById('scope').value;
  const res = await fetch('/api/run', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope }),
  });
  if (res.status === 409) { logLine('A run is already in progress.'); return; }
  if (res.status === 400) { logLine('Invalid scope.'); return; }
  setPhase('running');
});

refreshStatus();
connectEvents();
