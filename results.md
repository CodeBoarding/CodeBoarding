# PR626 paired evaluation results

Both engines use Gemini 3.8 Flash, depth cap 3, three workers, no LLM judge. Incremental runs use their own engine’s fresh full baseline. Missing runs and unavailable token counts are not passes or zeroes.

## release

| Test | Base: met / violated / errors | PR: met / violated / errors | Base seconds | PR seconds |
|---|---:|---:|---:|---:|
| codeboarding | 5 / 0 / 0 | 5 / 0 / 0 | 269 | 260 |
| eshop | 5 / 0 / 0 | 5 / 0 / 0 | 358 | 352 |
| failproofai | 5 / 0 / 0 | 5 / 0 / 0 | 427 | 444 |
| gson | 5 / 0 / 0 | 5 / 0 / 0 | 302 | 347 |
| hono | 5 / 0 / 0 | 5 / 0 / 0 | 194 | 171 |
| jsoup | 5 / 0 / 0 | 5 / 0 / 0 | 246 | 274 |
| junit4 | 5 / 0 / 0 | 5 / 0 / 0 | 255 | 228 |
| mermaid | 5 / 0 / 0 | 5 / 0 / 0 | 311 | 322 |
| polly | 5 / 0 / 0 | 5 / 0 / 0 | 394 | 397 |
| serilog | 5 / 0 / 0 | 5 / 0 / 0 | 269 | 234 |

base: 10 recorded runs; 50 criteria met; 0 violated; 0 execution errors.
head: 10 recorded runs; 50 criteria met; 0 violated; 0 execution errors.

## full

| Test | Base: met / violated / errors | PR: met / violated / errors | Base seconds | PR seconds |
|---|---:|---:|---:|---:|
| click-before-colorama-removal | not run | 5 / 0 / 0 | — | 93 |
| click-before-get-strerror-removal | not run | 5 / 0 / 0 | — | 84 |
| click-before-typing-modernisation | not run | 5 / 0 / 0 | — | 86 |

base: 0 recorded runs; 0 criteria met; 0 violated; 0 execution errors.
head: 3 recorded runs; 15 criteria met; 0 violated; 0 execution errors.

## incremental

| Test | Base: met / violated / errors | PR: met / violated / errors | Base seconds | PR seconds |
|---|---:|---:|---:|---:|

base: 0 recorded runs; 0 criteria met; 0 violated; 0 execution errors.
head: 0 recorded runs; 0 criteria met; 0 violated; 0 execution errors.

## e2e

| Test | Base: met / violated / errors | PR: met / violated / errors | Base seconds | PR seconds |
|---|---:|---:|---:|---:|

base: 0 recorded runs; 0 criteria met; 0 violated; 0 execution errors.
head: 0 recorded runs; 0 criteria met; 0 violated; 0 execution errors.

## Complete criterion outcomes

### release/base/codeboarding

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 146 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 146 file(s) analysed of 167 eligible (87%); 0 ignored-but-analysed, 21 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/head/codeboarding

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 146 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 146 file(s) analysed of 167 eligible (87%); 0 ignored-but-analysed, 21 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/base/eshop

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 481 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 481 file(s) analysed of 498 eligible (95%); 0 ignored-but-analysed, 27 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/head/eshop

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 481 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 481 file(s) analysed of 498 eligible (95%); 0 ignored-but-analysed, 27 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/base/failproofai

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 388 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 388 file(s) analysed of 289 eligible (97%); 0 ignored-but-analysed, 9 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/head/failproofai

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 388 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 388 file(s) analysed of 289 eligible (97%); 0 ignored-but-analysed, 9 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/base/gson

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 109 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 109 file(s) analysed of 114 eligible (96%); 0 ignored-but-analysed, 5 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/head/gson

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 109 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 109 file(s) analysed of 114 eligible (96%); 0 ignored-but-analysed, 5 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/base/hono

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 164 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 164 file(s) analysed of 213 eligible (64%); 0 ignored-but-analysed, 76 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/head/hono

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 164 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 164 file(s) analysed of 213 eligible (64%); 0 ignored-but-analysed, 76 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/base/jsoup

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 86 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 86 file(s) analysed of 93 eligible (92%); 0 ignored-but-analysed, 7 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/head/jsoup

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 86 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 86 file(s) analysed of 93 eligible (92%); 0 ignored-but-analysed, 7 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/base/junit4

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 204 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 204 file(s) analysed of 215 eligible (94%); 0 ignored-but-analysed, 12 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/head/junit4

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 204 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 204 file(s) analysed of 215 eligible (94%); 0 ignored-but-analysed, 12 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/base/mermaid

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 462 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 462 file(s) analysed of 626 eligible (73%); 0 ignored-but-analysed, 166 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/head/mermaid

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 462 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 462 file(s) analysed of 626 eligible (73%); 0 ignored-but-analysed, 166 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/base/polly

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 459 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 459 file(s) analysed of 479 eligible (96%); 0 ignored-but-analysed, 20 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/head/polly

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 459 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 459 file(s) analysed of 479 eligible (96%); 0 ignored-but-analysed, 20 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/base/serilog

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 109 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 109 file(s) analysed of 112 eligible (97%); 0 ignored-but-analysed, 3 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/head/serilog

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 109 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 109 file(s) analysed of 112 eligible (97%); 0 ignored-but-analysed, 3 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/head/click-before-colorama-removal

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 16 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 16 file(s) analysed of 18 eligible (89%); 0 ignored-but-analysed, 2 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/head/click-before-get-strerror-removal

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 15 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 15 file(s) analysed of 19 eligible (79%); 0 ignored-but-analysed, 4 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/head/click-before-typing-modernisation

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 15 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 15 file(s) analysed of 17 eligible (88%); 0 ignored-but-analysed, 2 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

