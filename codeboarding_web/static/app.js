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
    // Compound parent: labeled box around its children
    selector: 'node:parent',
    style: {
      'text-valign': 'top',
      'text-halign': 'center',
      'font-size': 11,
      'padding': '16px',
      'background-color': '#1e1e14',
      'background-opacity': 0.6,
      'border-color': '#FFC107',
      'border-width': 2,
    },
  },
  {
    // Expandable leaf nodes: gold accent border
    selector: 'node[?expandable]:not(:parent)',
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

// ── SSE connection state ─────────────────────────────────────────────────────
let sseDown = false;

// ── Expansion state ──────────────────────────────────────────────────────────
// Set of node ids that are currently expanded (have children in the graph).
const expanded = new Set();

// ── Repo path (from /api/status) ─────────────────────────────────────────────
let repoPath = '';

// ── File link helper ─────────────────────────────────────────────────────────
// Returns a DOM element: <a vscode://file/…> when repoPath is known, else <code>.
// XSS-safe: all paths are set via textContent/setAttribute, never innerHTML.
function fileLink(relFile, line) {
  if (repoPath) {
    const a = document.createElement('a');
    const abs = repoPath + '/' + relFile;
    a.href = 'vscode://file/' + abs + (line ? ':' + line : '');
    a.textContent = relFile;
    a.title = relFile;
    return a;
  }
  const code = document.createElement('code');
  code.textContent = relFile;
  return code;
}

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

// ── Collapse-all control ─────────────────────────────────────────────────────
function updateCollapseAll() {
  const btn = document.getElementById('collapse-all');
  if (btn) btn.style.display = expanded.size > 0 ? '' : 'none';
}

// ── Expand glyph ─────────────────────────────────────────────────────────────
// Always recomputes from baseLabel so appends are never cumulative.
// Appends badge counts (warnings/modifications) and ⊕/⊖ glyph.
function refreshGlyph(node) {
  const d = node.data();
  if (!d.expandable) return;
  const base = d.baseLabel || d.label;
  node.data('baseLabel', base);
  const badgeParts = [];
  if (d.warnings > 0) badgeParts.push('⚠' + d.warnings);
  if (d.modifications > 0) badgeParts.push('✎' + d.modifications);
  const badges = badgeParts.join('  ');
  const glyphSuffix = expanded.has(node.id()) ? '  ⊖' : '  ⊕';
  node.data('label', base + (badges ? '\n' + badges : '') + glyphSuffix);
}

// Initialize glyph when elements are first added (sets baseLabel once).
function initGlyphs(nodes) {
  nodes.forEach((node) => {
    if (node.data('expandable')) {
      const base = node.data('baseLabel') || node.data('label');
      node.data('baseLabel', base);
      const d = node.data();
      const badgeParts = [];
      if (d.warnings > 0) badgeParts.push('⚠' + d.warnings);
      if (d.modifications > 0) badgeParts.push('✎' + d.modifications);
      const badges = badgeParts.join('  ');
      node.data('label', base + (badges ? '\n' + badges : '') + '  ⊕');
    }
  });
}

// ── Graph rendering ──────────────────────────────────────────────────────────
function renderGraph(elements) {
  cy.elements().remove();
  expanded.clear();

  // Stamp baseLabel before adding so glyphs work correctly.
  const withBase = elements.map((e) => {
    if (e.data && e.data.expandable) {
      const base = e.data.label;
      const badgeParts = [];
      if (e.data.warnings > 0) badgeParts.push('⚠' + e.data.warnings);
      if (e.data.modifications > 0) badgeParts.push('✎' + e.data.modifications);
      const badges = badgeParts.join('  ');
      return { ...e, data: { ...e.data, baseLabel: base, label: base + (badges ? '\n' + badges : '') + '  ⊕' } };
    }
    return e;
  });

  cy.add(withBase);
  cy.layout({ name: 'dagre', rankDir: 'LR', fit: false }).run();
  cy.fit(undefined, 30);
  hasLaidOut = true;
  clearDetail();
  updateCollapseAll();
}

// In-place apply: preserve pan/zoom, add/remove/update, highlight new nodes.
// Used ONLY at overview level during live streaming (gated on expanded.size===0).
function applyElements(elements) {
  if (expanded.size > 0) return; // don't clobber an expanded state

  const pan = cy.pan();
  const zoom = cy.zoom();
  const incoming = new Map(elements.map((e) => [e.data.id, e]));
  const existing = new Set(cy.elements().map((e) => e.id()));

  cy.elements().forEach((e) => { if (!incoming.has(e.id())) e.remove(); });

  const added = [];
  incoming.forEach((e, id) => {
    if (existing.has(id)) {
      const n = cy.getElementById(id);
      n.data(e.data);
      n.data('baseLabel', e.data.label);
      refreshGlyph(n);
    } else {
      const el = cy.add(e);
      if (el.isNode()) added.push(el);
    }
  });

  // Apply glyphs to any newly added nodes.
  initGlyphs(added);

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

// ── Overview load ────────────────────────────────────────────────────────────
async function loadOverview() {
  try {
    const res = await fetch('/api/diagram.json');
    if (!res.ok) { logLine('Failed to load overview diagram'); return; }
    const data = await res.json();
    renderGraph(data.elements);
  } catch (_) {
    logLine('request failed: /api/diagram.json');
  }
}

// ── Compound expand/collapse ─────────────────────────────────────────────────
async function expandNode(node) {
  const pid = node.id();
  if (expanded.has(pid)) return;

  const componentId = node.data('componentId');
  let data;
  try {
    const res = await fetch('/api/diagram/' + componentId);
    if (!res.ok) { logLine('No sub-graph for: ' + (node.data('baseLabel') || node.data('label'))); return; }
    data = await res.json();
  } catch (_) {
    logLine('request failed: /api/diagram/' + componentId);
    return;
  }

  const subElements = data.elements || [];

  // Separate nodes and edges so we can validate edge endpoints.
  const subNodes = subElements.filter((e) => !e.data.source);
  const subEdges = subElements.filter((e) => e.data.source);

  // Build the set of new node ids (prefixed) for edge validation.
  const addedIds = new Set(subNodes.map((e) => pid + '::' + e.data.id));

  const newNodes = subNodes.map((e) => {
    const newId = pid + '::' + e.data.id;
    const baseLabel = e.data.label || e.data.id;
    let label = baseLabel;
    if (e.data.expandable) {
      const badgeParts = [];
      if (e.data.warnings > 0) badgeParts.push('⚠' + e.data.warnings);
      if (e.data.modifications > 0) badgeParts.push('✎' + e.data.modifications);
      const badges = badgeParts.join('  ');
      label = baseLabel + (badges ? '\n' + badges : '') + '  ⊕';
    }
    return {
      data: {
        ...e.data,
        id: newId,
        parent: pid,
        baseLabel,
        label,
        // Keep componentId and expandable intact for recursive expand.
      },
    };
  });

  const newEdges = subEdges
    .filter((e) => addedIds.has(pid + '::' + e.data.source) && addedIds.has(pid + '::' + e.data.target))
    .map((e) => ({
      data: {
        ...e.data,
        id: pid + '::' + (e.data.id || (e.data.source + '-' + e.data.target)),
        source: pid + '::' + e.data.source,
        target: pid + '::' + e.data.target,
      },
    }));

  cy.add([...newNodes, ...newEdges]);
  expanded.add(pid);
  refreshGlyph(node);
  cy.layout({ name: 'dagre', rankDir: 'LR', fit: false }).run();
  cy.fit(cy.getElementById(pid), 50);
  updateCollapseAll();
}

function collapseNode(node) {
  const pid = node.id();

  // Remove all descendant nodes (their edges auto-remove).
  cy.nodes().filter((n) => n.id().startsWith(pid + '::')).remove();

  // Clean up expanded tracking for this node and all its nested children.
  [...expanded].forEach((id) => {
    if (id === pid || id.startsWith(pid + '::')) expanded.delete(id);
  });

  refreshGlyph(node);

  const pan = cy.pan();
  const zoom = cy.zoom();
  cy.layout({ name: 'dagre', rankDir: 'LR', fit: false }).run();
  cy.pan(pan);
  cy.zoom(zoom);

  updateCollapseAll();
}

function toggleNode(node) {
  if (expanded.has(node.id())) {
    collapseNode(node);
  } else {
    expandNode(node);
  }
}

// ── Detail sidebar state ─────────────────────────────────────────────────────
// Tracks which componentId has a cached diff so we only fetch once per selection.
let _diffCachedFor = null;
// Tracks the currently selected componentId to guard against stale diff fetches.
let selectedComponentId = null;

function _switchTab(tabName) {
  document.querySelectorAll('.tab-btn').forEach((b) => {
    b.classList.toggle('active', b.dataset.tab === tabName);
  });
  document.querySelectorAll('.tab-panel').forEach((p) => {
    p.classList.toggle('hidden', p.id !== 'tab-' + tabName);
  });
  if (tabName === 'modifications') {
    _loadDiffIfNeeded();
  }
}

function _loadDiffIfNeeded() {
  const componentId = document.getElementById('detail-content').dataset.componentId;
  if (!componentId || _diffCachedFor === componentId) return;
  _diffCachedFor = componentId;

  const summary = document.getElementById('modifications-summary');
  const fileList = document.getElementById('modifications-file-list');
  const diffPre = document.getElementById('modifications-diff');
  summary.textContent = 'Loading…';
  fileList.innerHTML = '';
  diffPre.innerHTML = '';

  fetch('/api/component/' + componentId + '/diff')
    .then((r) => r.json())
    .then((data) => {
      // Guard against stale results: discard if selection changed.
      if (componentId !== selectedComponentId) return;

      const files = data.files || [];
      const diffText = data.diff || '';
      fileList.innerHTML = '';
      diffPre.innerHTML = '';

      if (files.length === 0) {
        summary.textContent = 'No modifications.';
        return;
      }

      files.forEach((f) => {
        const li = document.createElement('li');
        li.appendChild(fileLink(f));
        fileList.appendChild(li);
      });

      if (!diffText) {
        summary.textContent = 'Modified files (' + files.length + ') — new/untracked, no diff to show';
        return;
      }

      summary.textContent = 'Modified files (' + files.length + ')';
      diffText.split('\n').forEach((line) => {
        const span = document.createElement('span');
        if (line.startsWith('+++') || line.startsWith('---')) {
          // file header lines — plain
        } else if (line.startsWith('+')) {
          span.className = 'add';
        } else if (line.startsWith('-')) {
          span.className = 'del';
        } else if (line.startsWith('@@')) {
          span.className = 'hunk';
        }
        span.textContent = line;
        diffPre.appendChild(span);
        diffPre.appendChild(document.createTextNode('\n'));
      });
    })
    .catch(() => {
      // Guard against stale results on error as well.
      if (componentId === selectedComponentId) {
        document.getElementById('modifications-summary').textContent = 'Failed to load diff.';
      }
    });
}

// ── Detail sidebar ───────────────────────────────────────────────────────────
function clearDetail() {
  document.getElementById('detail-empty').classList.remove('hidden');
  document.getElementById('detail-content').classList.add('hidden');
  cy.nodes().unselect();
}

function renderDetail(node) {
  const d = node.data();
  const nodeId = node.id();

  // Reset tab to Overview and clear diff cache for new selection.
  _diffCachedFor = null;
  selectedComponentId = d.componentId || null;
  _switchTab('overview');

  document.getElementById('detail-empty').classList.add('hidden');
  const content = document.getElementById('detail-content');
  content.classList.remove('hidden');
  content.dataset.componentId = d.componentId || '';

  document.getElementById('detail-name').textContent = d.baseLabel || d.label || d.id;
  document.getElementById('detail-desc').textContent = d.description || '';

  // Key Entities
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

  // Source Files (Overview tab) — F3: clickable vscode:// links
  const sourceFiles = d.sourceFiles || [];
  const sourcesSection = document.getElementById('detail-sources');
  const sourceList = document.getElementById('detail-source-list');
  const sourcesHeader = document.getElementById('detail-sources-header');
  sourceList.innerHTML = '';
  if (sourceFiles.length) {
    sourcesSection.classList.remove('hidden');
    sourcesHeader.textContent = 'Source Files (' + sourceFiles.length + ')';
    sourceFiles.forEach((f) => {
      const li = document.createElement('li');
      li.appendChild(fileLink(f));
      sourceList.appendChild(li);
    });
  } else {
    sourcesSection.classList.add('hidden');
  }

  // Warnings tab — F2: per-file detail from fileWarnings
  const warnings = d.warnings || 0;
  const fileWarnings = d.fileWarnings || [];
  document.getElementById('warnings-summary').textContent = warnings + (warnings === 1 ? ' warning' : ' warnings');
  const warningsFileList = document.getElementById('warnings-file-list');
  warningsFileList.innerHTML = '';
  if (warnings > 0 && fileWarnings.length > 0) {
    fileWarnings.forEach(({ file, warnings: count }) => {
      const li = document.createElement('li');
      li.appendChild(fileLink(file));
      const sep = document.createTextNode(' — ' + count);
      li.appendChild(sep);
      warningsFileList.appendChild(li);
    });
  } else if (warnings === 0) {
    const li = document.createElement('li');
    li.textContent = 'No warnings.';
    warningsFileList.appendChild(li);
  }

  // Modifications tab — clear display (loaded lazily)
  document.getElementById('modifications-summary').textContent = '';
  document.getElementById('modifications-file-list').innerHTML = '';
  document.getElementById('modifications-diff').innerHTML = '';

  // Expand button
  const expandBtn = document.getElementById('detail-expand');
  if (d.expandable) {
    expandBtn.classList.remove('hidden');
    const isExpanded = expanded.has(nodeId);
    expandBtn.textContent = isExpanded ? 'Collapse ⊖' : 'Expand ⊕';
    expandBtn.onclick = () => toggleNode(cy.getElementById(nodeId));
  } else {
    expandBtn.classList.add('hidden');
    expandBtn.onclick = null;
  }
}

// ── Tab bar interaction ───────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach((btn) => {
  btn.addEventListener('click', () => _switchTab(btn.dataset.tab));
});

// ── Copy Context button ───────────────────────────────────────────────────────
document.getElementById('detail-copy').addEventListener('click', () => {
  const content = document.getElementById('detail-content');
  if (content.classList.contains('hidden')) return;

  const name = document.getElementById('detail-name').textContent;
  const desc = document.getElementById('detail-desc').textContent;

  const entities = [];
  document.querySelectorAll('#detail-entity-list li').forEach((li) => {
    const a = li.querySelector('a');
    const code = li.querySelector('code');
    entities.push('- `' + (a ? a.textContent : code ? code.textContent : '') + '`');
  });

  const srcFiles = [];
  document.querySelectorAll('#detail-source-list li code').forEach((c) => {
    srcFiles.push('- ' + c.textContent);
  });

  let md = '## ' + name + '\n\n' + (desc || '') + '\n';
  if (entities.length) {
    md += '\n### Key Code Entities\n' + entities.join('\n') + '\n';
  }
  if (srcFiles.length) {
    md += '\n### Source Files\n' + srcFiles.join('\n') + '\n';
  }

  navigator.clipboard.writeText(md)
    .then(() => logLine('context copied'))
    .catch(() => logLine('copy failed'));
});

// ── Cytoscape interaction handlers ───────────────────────────────────────────
cy.on('tap', 'node', (evt) => {
  renderDetail(evt.target);
});

cy.on('tap', (evt) => {
  if (evt.target === cy) clearDetail();
});

cy.on('dbltap', 'node[?expandable]', (evt) => {
  toggleNode(evt.target);
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

// ── Collapse-all handler ─────────────────────────────────────────────────────
document.getElementById('collapse-all').addEventListener('click', () => {
  loadOverview();
});

// ── Status / bootstrap ───────────────────────────────────────────────────────
async function refreshStatus() {
  try {
    const s = await (await fetch('/api/status')).json();
    document.getElementById('project').textContent = s.project;
    setPhase(s.phase);
    if (s.repo_path) repoPath = s.repo_path;
    if (s.has_baseline) loadOverview();
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
  src.addEventListener('step_error', (e) => { sseDown = false; const d = JSON.parse(e.data); logLine('✗ ' + (d.step || 'step') + (d.error ? ': ' + d.error : '')); });
  src.addEventListener('phase_change', (e) => { sseDown = false; logLine('— ' + JSON.parse(e.data).step); });

  src.addEventListener('error', () => {
    if (!sseDown && (src.readyState === EventSource.CLOSED || src.readyState === EventSource.CONNECTING)) {
      sseDown = true;
      logLine('connection lost; reconnecting…');
    }
  });

  src.addEventListener('diagram_delta', (e) => {
    // Only apply in-place updates at the overview when nothing is expanded.
    if (expanded.size === 0) {
      applyElements(JSON.parse(e.data).elements);
    }
  });

  src.addEventListener('run_end', async () => {
    // Capture expansion order before reloading (parents before their children).
    const saved = [...expanded];
    await loadOverview();
    // Re-expand in insertion order so parent compounds exist before children.
    for (const id of saved) {
      try {
        const n = cy.getElementById(id);
        if (n.nonempty() && n.data('expandable') && !expanded.has(n.id())) {
          await expandNode(n);
        }
      } catch (_) {
        // Skip nodes that no longer exist in the updated graph.
      }
    }
    setPhase('done');
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

// ── Run / Watch controls ─────────────────────────────────────────────────────
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
updateCollapseAll();
refreshStatus();
connectEvents();
