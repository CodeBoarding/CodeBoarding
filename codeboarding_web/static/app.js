// codeboarding_web/static/app.js

// ── Cytoscape style ──────────────────────────────────────────────────────────
const STYLE = [
  {
    selector: 'node',
    style: {
      'shape': 'round-rectangle',
      'label': 'data(label)',
      'color': '#E6E6E6',
      'background-color': '#262626',
      'border-color': '#3a3a3a',
      'border-width': 1.5,
      'text-valign': 'center',
      'text-halign': 'center',
      'font-size': 11,
      'width': 'label',
      'padding': '10px',
      'text-wrap': 'wrap',
      'text-max-width': 160,
    },
  },
  {
    // Expandable nodes: gold accent border so they're visually distinct
    selector: 'node[?expandable]',
    style: {
      'border-color': '#FFC107',
      'border-width': 2.5,
    },
  },
  {
    selector: 'node:selected',
    style: {
      'border-color': '#FFC107',
      'border-width': 3,
      'background-color': '#2e2e1a',
    },
  },
  {
    selector: 'node.changed',
    style: {
      'background-color': '#1a2e1a',
      'border-color': '#4CAF50',
      'border-width': 2.5,
    },
  },
  {
    selector: 'edge',
    style: {
      'width': 1.5,
      'line-color': '#555',
      'target-arrow-color': '#555',
      'target-arrow-shape': 'triangle',
      'curve-style': 'bezier',
      'label': 'data(label)',
      'font-size': 7,
      'color': '#888',
    },
  },
];

// ── Cytoscape instance ───────────────────────────────────────────────────────
const cy = cytoscape({ container: document.getElementById('cy'), elements: [], style: STYLE });
let hasLaidOut = false;

// ── In-flight navigation guard ───────────────────────────────────────────────
let navSeq = 0;

// ── SSE connection state ─────────────────────────────────────────────────────
let sseDown = false;

// ── Breadcrumb state ─────────────────────────────────────────────────────────
// Each entry: { id: string|null, label: string }
// index 0 is always Overview (id=null).
let breadcrumbStack = [{ id: null, label: 'Overview' }];

// ── Log helpers ──────────────────────────────────────────────────────────────
function logLine(text) {
  const el = document.createElement('div');
  el.className = 'log-line';
  el.textContent = text;
  document.getElementById('log').prepend(el);
}

function setPhase(phase) {
  const el = document.getElementById('phase');
  el.textContent = phase;
  el.className = 'phase ' + phase;
}

// ── Breadcrumb rendering ─────────────────────────────────────────────────────
function renderBreadcrumb() {
  const nav = document.getElementById('breadcrumb');
  nav.innerHTML = '';
  breadcrumbStack.forEach((crumb, i) => {
    if (i > 0) {
      const sep = document.createElement('span');
      sep.className = 'crumb-sep';
      sep.textContent = '›';
      nav.appendChild(sep);
    }
    const span = document.createElement('span');
    span.className = 'crumb' + (i === breadcrumbStack.length - 1 ? ' active' : '');
    span.textContent = crumb.label;
    if (i < breadcrumbStack.length - 1) {
      span.addEventListener('click', () => navigateTo(i));
    }
    nav.appendChild(span);
  });
}

// ── Graph rendering ──────────────────────────────────────────────────────────
function renderGraph(elements, opts) {
  const fit = opts && opts.fit !== false;
  const pan = cy.pan();
  const zoom = cy.zoom();

  // Replace entire graph
  cy.elements().remove();
  cy.add(elements);

  cy.layout({ name: 'dagre', rankDir: 'LR', fit: false }).run();

  if (fit) {
    cy.fit(undefined, 30);
  } else {
    cy.pan(pan);
    cy.zoom(zoom);
  }
  hasLaidOut = true;
  clearDetail();
}

// In-place apply: preserve pan/zoom, add/remove/update, highlight new nodes.
// Used ONLY at overview level during live streaming.
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

// ── Navigation ───────────────────────────────────────────────────────────────
async function navigateTo(index) {
  breadcrumbStack = breadcrumbStack.slice(0, index + 1);
  renderBreadcrumb();

  const entry = breadcrumbStack[index];
  const url = entry.id === null ? '/api/diagram.json' : '/api/diagram/' + entry.id;
  const token = ++navSeq;
  try {
    const res = await fetch(url);
    if (!res.ok) {
      logLine('Failed to load diagram for: ' + (entry.label || 'overview'));
      return;
    }
    if (token !== navSeq) return; // superseded by a newer navigation
    const data = await res.json();
    renderGraph(data.elements, { fit: true });
  } catch (_) {
    logLine('request failed: ' + url);
  }
}

async function drillInto(componentId, label) {
  const url = '/api/diagram/' + componentId;
  const token = ++navSeq;
  try {
    const res = await fetch(url);
    if (!res.ok) {
      logLine('No sub-graph for: ' + label);
      return;
    }
    if (token !== navSeq) return; // superseded by a newer navigation
    const data = await res.json();
    breadcrumbStack.push({ id: componentId, label: label });
    renderBreadcrumb();
    renderGraph(data.elements, { fit: true });
  } catch (_) {
    logLine('request failed: ' + url);
  }
}

function isAtOverview() {
  return breadcrumbStack.length === 1 && breadcrumbStack[0].id === null;
}

// ── Detail sidebar ───────────────────────────────────────────────────────────
function clearDetail() {
  document.getElementById('detail-empty').classList.remove('hidden');
  document.getElementById('detail-content').classList.add('hidden');
  cy.nodes().unselect();
}

function renderDetail(node) {
  const d = node.data();
  document.getElementById('detail-empty').classList.add('hidden');
  document.getElementById('detail-content').classList.remove('hidden');
  document.getElementById('detail-name').textContent = d.label || d.id;
  document.getElementById('detail-desc').textContent = d.description || '';

  const list = document.getElementById('detail-entity-list');
  list.innerHTML = '';
  const entities = d.keyEntities || [];
  const entitySection = document.getElementById('detail-entities');
  entitySection.style.display = entities.length ? '' : 'none';

  entities.forEach((ent) => {
    const li = document.createElement('li');
    const lineInfo = ent.startLine != null ? ':' + ent.startLine + (ent.endLine != null ? '-' + ent.endLine : '') : '';
    const label = (ent.qname || '') + lineInfo;
    if (ent.openUrl) {
      const a = document.createElement('a');
      a.href = ent.openUrl;
      a.textContent = label;
      a.title = ent.file || ent.openUrl;
      li.appendChild(a);
    } else {
      const code = document.createElement('code');
      code.textContent = label;
      li.appendChild(code);
    }
    list.appendChild(li);
  });

  const expandBtn = document.getElementById('detail-expand');
  if (d.expandable) {
    expandBtn.classList.remove('hidden');
    expandBtn.onclick = () => drillInto(d.componentId, d.label || d.id);
  } else {
    expandBtn.classList.add('hidden');
    expandBtn.onclick = null;
  }
}

// ── Cytoscape interaction handlers ───────────────────────────────────────────
cy.on('tap', 'node', (evt) => {
  renderDetail(evt.target);
});

cy.on('tap', (evt) => {
  if (evt.target === cy) clearDetail();
});

cy.on('dbltap', 'node[?expandable]', (evt) => {
  const d = evt.target.data();
  drillInto(d.componentId, d.label || d.id);
});

// ── Toolbar handlers ─────────────────────────────────────────────────────────
document.getElementById('tb-zoomin').addEventListener('click', () => {
  cy.zoom({ level: cy.zoom() * 1.2, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
});

document.getElementById('tb-zoomout').addEventListener('click', () => {
  cy.zoom({ level: cy.zoom() / 1.2, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
});

document.getElementById('tb-fit').addEventListener('click', () => {
  cy.fit(undefined, 30);
});

document.getElementById('tb-relayout').addEventListener('click', () => {
  const pan = cy.pan();
  const zoom = cy.zoom();
  cy.layout({ name: 'dagre', rankDir: 'LR', fit: false }).run();
  cy.pan(pan);
  cy.zoom(zoom);
});

// ── Log toggle ───────────────────────────────────────────────────────────────
document.getElementById('log-toggle').addEventListener('click', () => {
  const log = document.getElementById('log');
  const btn = document.getElementById('log-toggle');
  const collapsed = log.classList.toggle('collapsed');
  btn.textContent = collapsed ? '▲' : '▼';
});

// ── Initial data load ────────────────────────────────────────────────────────
async function loadDiagram() {
  const entry = breadcrumbStack[breadcrumbStack.length - 1];
  const url = entry.id === null ? '/api/diagram.json' : '/api/diagram/' + entry.id;
  try {
    const res = await fetch(url);
    if (res.ok) {
      const data = await res.json();
      if (isAtOverview()) {
        applyElements(data.elements);
      } else {
        renderGraph(data.elements, { fit: true });
      }
    }
  } catch (_) {
    logLine('request failed: ' + url);
  }
}

async function refreshStatus() {
  try {
    const s = await (await fetch('/api/status')).json();
    document.getElementById('project').textContent = s.project;
    setPhase(s.phase);
    if (s.has_baseline) loadDiagram();
    document.getElementById('watch').checked = s.watch_enabled;
  } catch (_) {
    logLine('request failed: /api/status');
  }
}

// ── SSE event listeners ──────────────────────────────────────────────────────
function connectEvents() {
  const src = new EventSource('/api/events');

  src.addEventListener('step_start', (e) => { sseDown = false; logLine('▶ ' + JSON.parse(e.data).step); });
  src.addEventListener('step_end', (e) => { sseDown = false; logLine('✓ ' + JSON.parse(e.data).step); });
  src.addEventListener('step_error', (e) => { const d = JSON.parse(e.data); logLine('✗ ' + (d.step || 'step') + (d.error ? ': ' + d.error : '')); });
  src.addEventListener('phase_change', (e) => { sseDown = false; logLine('— ' + JSON.parse(e.data).step); });

  src.addEventListener('error', () => {
    if (!sseDown && (src.readyState === EventSource.CLOSED || src.readyState === EventSource.CONNECTING)) {
      sseDown = true;
      logLine('connection lost; reconnecting…');
    }
  });

  src.addEventListener('diagram_delta', (e) => {
    // Only apply in-place updates at the overview to avoid clobbering a drilled-in view
    if (isAtOverview()) {
      applyElements(JSON.parse(e.data).elements);
    }
  });

  src.addEventListener('run_end', () => {
    setPhase('done');
    // Re-fetch the current breadcrumb level (preserves drilled-in view after a run)
    loadDiagram();
  });

  src.addEventListener('run_error', (e) => {
    setPhase('error');
    logLine('ERROR: ' + JSON.parse(e.data).error);
  });

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

// ── Run / Watch controls (unchanged behavior) ────────────────────────────────
document.getElementById('watch').addEventListener('change', async (e) => {
  try {
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
  } catch (_) {
    logLine('request failed: /api/watch');
    e.target.checked = !e.target.checked;
  }
});

document.getElementById('run').addEventListener('click', async () => {
  const scope = document.getElementById('scope').value;
  try {
    const res = await fetch('/api/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope }),
    });
    if (res.status === 409) { logLine('A run is already in progress.'); return; }
    if (res.status === 400) { logLine('Invalid scope.'); return; }
    setPhase('running');
  } catch (_) {
    logLine('request failed: /api/run');
  }
});

// ── Bootstrap ────────────────────────────────────────────────────────────────
renderBreadcrumb();
refreshStatus();
connectEvents();
