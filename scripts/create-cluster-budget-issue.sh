#!/usr/bin/env bash
gh issue create \
  --repo CodeBoarding/CodeBoarding \
  --title "MAX_LLM_CLUSTERS budget (50) may need tuning for polyglot repos" \
  --body "$(cat <<'BODY'
## Summary

`MAX_LLM_CLUSTERS = 50` in `static_analyzer/cluster_helpers.py:35` caps the total clusters across all languages. For polyglot repos (JS + Python + TS + C#), the proportional split gives each language fewer clusters, potentially losing architectural detail.

## Observed Behavior

On a 4-language monorepo (1879 files: 725 C#, 559 TS, 43 Python, 11 JS):
- C# raw: ~100+ clusters → squished to ~15
- TypeScript: 784 clusters → 23
- JavaScript: 45 → 20
- Python: 15 → 7

The compact mode prompt then sends all 50 clusters to the LLM, which already struggles with high-numbered cluster IDs (see compact mode validation scores).

## Questions

1. Should `MAX_LLM_CLUSTERS` scale with language count? (e.g., 50 per language, or 30 * num_languages)
2. Should it be configurable via `config.toml`?
3. Would batching clusters (15-20 per LLM call) be more effective than a hard cap?

## Related

The compact mode cluster coverage issue — LLM misses clusters 24+ on first attempt, needing validation retry. A higher budget worsens this; batching would fix both problems.

## Environment

- CodeBoarding main + C# support branch
- 4-language monorepo test case
BODY
)"
