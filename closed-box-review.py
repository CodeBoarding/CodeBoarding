"""Compare the published implementations and a no-exposed-files proposal in isolation."""
import ast
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from static_analyzer.clustering.names import draft as d
from tests.static_analyzer.names.conftest import units_from_layout

BASE = '79db8c8010dde1b6e0394002c5e68312da5a1b26'
HEAD = '9e86d3d18388e467ee64210552f5a57c28df89fa'


def install(commit):
    source = subprocess.check_output(['git', 'show', f'{commit}:static_analyzer/clustering/names/draft.py'], cwd=ROOT, text=True)
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == '_file_rules')
    exec(compile(ast.Module(body=[node], type_ignores=[]), f'{commit}:draft.py', 'exec'), d.__dict__)
    return d._file_rules


def proposed(scope_id, units, role_words, grouper, links):
    frontier = d.walk(d.Trie(units), role_words, transpose=False, layers=True)
    candidates = [c for c in frontier.candidates if c.kind == d.BOX and all(p not in frontier.opened for p in c.prefixes)]
    boundaries = [p for c in candidates for p in c.prefixes]
    exposed = {u.key for u in units if not any(u.position[:len(p)] == p for p in boundaries)}
    if not exposed:
        candidates = []
        exposed = {u.key for u in units}
    candidates.extend(d.Candidate(f'{d.FILE}:{"/".join(k)}', d.FILE, d._label(k), prefixes=(k,)) for k in sorted(exposed))
    return d._grouped_rules(scope_id, units, candidates, role_words, grouper, d.FILES, links), d.FILES


def compare(units, links, scope_id='1.2', parts=(), roles=d.ROLE_WORDS):
    result = {}
    original_settle = d._settle
    for arm in ('base', 'head', 'proposal'):
        install(BASE if arm == 'base' else HEAD)
        if arm == 'proposal':
            d._file_rules = proposed
        trace = []
        def traced(sid, us, rules, role_words, rung, **kwargs):
            out = original_settle(sid, us, rules, role_words, rung, **kwargs)
            trace.append({'rung': rung, 'accepted': out is not None, 'rule_names': [r.name for r in rules]})
            return out
        d._settle = traced
        try:
            scope, partition = d.draft_scope(scope_id, units, roles, d.AffinityGrouper(), parts=parts, links=links)
        finally:
            d._settle = original_settle
        result[arm] = {'rung': scope.rung, 'trace': trace, 'spec': scope.to_dict(),
                       'members': {r.name: sorted(u.unit_id for u in partition.members[r.component_id]) for r in scope.rules}}
    return result


if __name__ == '__main__':
    layout = {f'payment_gateway/{provider}_{role}.py': [f'{provider}_{role}.run']
              for provider in ('stripe', 'paypal', 'adyen') for role in ('client', 'webhook', 'mapper')}
    layout |= {f'payment_ledger/ledger_{role}.py': [f'ledger_{role}.run'] for role in ('store', 'reader', 'writer', 'model')}
    links = {}
    for provider in ('stripe', 'paypal', 'adyen'):
        files = [p for p in layout if f'/{provider}_' in p]
        links.update({(a, b): 2 for a in files for b in files if a != b})
    result = compare(units_from_layout(layout), links)
    Path(__file__).with_suffix('.json').write_text(json.dumps(result, indent=2) + '\n')
    for arm, row in result.items():
        print(arm, row['rung'], [(name, len(files)) for name, files in row['members'].items()])
        print(' ladder:', [(x['rung'], x['accepted']) for x in row['trace']])
