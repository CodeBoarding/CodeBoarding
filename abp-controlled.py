"""Replay ABP's saved graph through complete hierarchies, preserving parent parts and project context."""
import hashlib
import json
from pathlib import Path
import pickle
import runpy
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from static_analyzer.clustering.names.inventory import PROJECT_MANIFESTS, units_from_graphs
from static_analyzer.clustering.names.replay import replay
from static_analyzer.clustering.names.spec import TreeSpec
from static_analyzer.clustering.service import unit_links

OUT = Path(__file__).parent
experiment = runpy.run_path(str(OUT / 'closed-box-review.py'))
d = experiment['d']
RUNS = Path('/home/ivan/StartUp/CodeBoarding-evals/.amp/in/artifacts/pr626/runs/release')
saved = RUNS / 'pr626-base/abp/output'
analysis = json.loads((saved / 'analysis.json').read_text())
recorded = TreeSpec.from_dict(analysis['metadata']['tree_spec'])
fingerprint = json.loads((saved / 'fingerprint.json').read_text())['files']
manifests = [p for p in fingerprint if Path(p).name in PROJECT_MANIFESTS or Path(p).suffix in ('.csproj', '.fsproj')]
graphs = pickle.loads((saved / 'static_analysis.pkl').read_bytes()).available_cfgs()
with tempfile.TemporaryDirectory(prefix='abp-project-context-', dir=ROOT / '.amp/in') as tmp:
    for name in manifests:
        path = Path(tmp) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    units = units_from_graphs(graphs, Path(tmp))
links = unit_links(graphs)
targets = {
    'AI': {u.unit_id for u in units if any(u.unit_id.startswith(p) for p in (
        'framework/src/Volo.Abp.AI/', 'framework/src/Volo.Abp.AI.Abstractions/'))},
    'Auditing': {u.unit_id for u in units if any(u.unit_id.startswith(p) for p in (
        'framework/src/Volo.Abp.Auditing/', 'framework/src/Volo.Abp.Auditing.Contracts/'))},
}
assert all(targets.values())
recorded_depth = max(1 if s == 'root' else len(s.split('.')) + 1 for s in recorded.scopes)
depth = recorded_depth + 1
result = {'source_revision': '329eaa80c3194e2effb990ff685a3199171d03bf',
          'graph_sha256': hashlib.sha256((saved / 'static_analysis.pkl').read_bytes()).hexdigest(),
          'units': len(units), 'links': len(links), 'depth': depth, 'recorded_depth': recorded_depth,
          'targets': {k: sorted(v) for k, v in targets.items()}, 'arms': {}}
original_settle = d._settle
for arm in ('base', 'head', 'proposal', 'base_repeat'):
    experiment['install'](experiment['BASE'] if arm in ('base', 'base_repeat') else experiment['HEAD'])
    if arm == 'proposal':
        d._file_rules = experiment['proposed']
    trace = []
    def traced(sid, us, rules, roles, rung, **kwargs):
        answer = original_settle(sid, us, rules, roles, rung, **kwargs)
        matched = [name for name, files in targets.items() if {u.unit_id for u in us} == files]
        if matched:
            trace.append({'target': matched[0], 'scope': sid, 'rung': rung,
                          'accepted': answer is not None, 'rules': [r.to_dict() for r in rules]})
        return answer
    d._settle = traced
    try:
        spec = d.draft_tree(units, d.AffinityGrouper(), depth, machinery=recorded.machinery, links=links)
    finally:
        d._settle = original_settle
    pending = {'root': units}
    scopes = {}
    for sid, scope in spec.scopes.items():
        current = pending[sid]
        partition = replay(current, scope, d.role_words_for(spec.machinery))
        pending.update(partition.members)
        covered = {u.unit_id for u in current}
        for name, files in targets.items():
            if files <= covered and len(covered) <= 2 * len(files):
                scopes[sid] = {'target': name, 'exact': covered == files, 'rung': scope.rung,
                               'files': sorted(covered), 'spec': scope.to_dict(),
                               'members': {r.name: sorted(u.unit_id for u in partition.members[r.component_id]) for r in scope.rules}}
    matches = all(sid in spec.scopes and spec.scopes[sid].to_dict() == scope.to_dict()
                  for sid, scope in recorded.scopes.items())
    result['arms'][arm] = {'scopes': scopes, 'trace': trace, 'recorded_scopes_equal': matches}
    if arm == 'base':
        base_spec = spec.to_dict()
    if arm == 'base_repeat':
        assert spec.to_dict() == base_spec
    print(arm, 'matches recorded baseline scopes:', matches)
    for sid, row in scopes.items():
        print(' ', sid, row['target'], 'exact', row['exact'], row['rung'], [(k, len(v)) for k, v in row['members'].items()])
    print(' trace:', [(x['target'], x['rung'], x['accepted']) for x in trace])
(OUT / 'abp-controlled.json').write_text(json.dumps(result, indent=2) + '\n')
assert result['arms']['base']['recorded_scopes_equal'], 'Reconstructed context does not reproduce the saved baseline.'
