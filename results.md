# PR626 paired evaluation results

Both engines use Gemini 3.8 Flash, depth cap 3, no LLM judge. Standard batches use three workers; the supplemental current-CodeBoarding pair uses one worker per sequential run. Incremental runs use their own engine’s fresh full baseline. Missing runs and unavailable token counts are not passes or zeroes.

## release

| Test | Base: met / violated / errors | PR: met / violated / errors | Base seconds | PR seconds |
|---|---:|---:|---:|---:|
| codeboarding | 5 / 0 / 0 | 5 / 0 / 0 | 269 | 260 |
| codeboarding-pr626 | 5 / 0 / 0 | 5 / 0 / 0 | 305 | 266 |
| eshop | 5 / 0 / 0 | 5 / 0 / 0 | 358 | 352 |
| failproofai | 5 / 0 / 0 | 5 / 0 / 0 | 427 | 444 |
| gson | 5 / 0 / 0 | 5 / 0 / 0 | 302 | 347 |
| hono | 5 / 0 / 0 | 5 / 0 / 0 | 194 | 171 |
| jsoup | 5 / 0 / 0 | 5 / 0 / 0 | 246 | 274 |
| junit4 | 5 / 0 / 0 | 5 / 0 / 0 | 255 | 228 |
| mermaid | 5 / 0 / 0 | 5 / 0 / 0 | 311 | 322 |
| polly | 5 / 0 / 0 | 5 / 0 / 0 | 394 | 397 |
| serilog | 5 / 0 / 0 | 5 / 0 / 0 | 269 | 234 |

base: 11 recorded runs; 55 criteria met; 0 violated; 0 execution errors.
head: 11 recorded runs; 55 criteria met; 0 violated; 0 execution errors.

## full

| Test | Base: met / violated / errors | PR: met / violated / errors | Base seconds | PR seconds |
|---|---:|---:|---:|---:|
| click-before-colorama-removal | 5 / 0 / 0 | 5 / 0 / 0 | 84 | 93 |
| click-before-get-strerror-removal | 5 / 0 / 0 | 5 / 0 / 0 | 68 | 84 |
| click-before-typing-modernisation | 5 / 0 / 0 | 5 / 0 / 0 | 80 | 86 |
| codeboarding-before-health-checks | 5 / 0 / 0 | 5 / 0 / 0 | 188 | 187 |
| codeboarding-before-local-server-removal | 5 / 0 / 0 | 5 / 0 / 0 | 189 | 245 |
| codeboarding-before-model-env-removal | 5 / 0 / 0 | 5 / 0 / 0 | 189 | 204 |
| gson-before-alternate-field-names | 5 / 0 / 0 | 5 / 0 / 0 | 480 | 364 |
| gson-before-gson-types-rename | 5 / 0 / 0 | 5 / 0 / 0 | 395 | 395 |
| gson-before-java-time-adapters | 5 / 0 / 0 | 5 / 0 / 0 | 427 | 320 |
| gson-before-reflection-helper-move | 5 / 0 / 0 | 5 / 0 / 0 | 361 | 408 |
| mediatr-before-fsharp-assembly-support | 5 / 0 / 0 | 5 / 0 / 0 | 215 | 190 |
| serilog-before-restricted-sink-forwarding | 5 / 0 / 0 | 5 / 0 / 0 | 370 | 277 |

base: 12 recorded runs; 60 criteria met; 0 violated; 0 execution errors.
head: 12 recorded runs; 60 criteria met; 0 violated; 0 execution errors.

## incremental

| Test | Base: met / violated / errors | PR: met / violated / errors | Base seconds | PR seconds |
|---|---:|---:|---:|---:|
| body-edit-no-call-change | 10 / 0 / 0 | 10 / 0 / 0 | 135 | 106 |
| capability-retired-no-unit-lost | 10 / 0 / 0 | 10 / 0 / 0 | 60 | 43 |
| cross-boundary-call-added | 9 / 1 / 0 | 9 / 1 / 0 | 248 | 273 |
| helpers-moved-between-classes | 9 / 1 / 0 | 9 / 1 / 0 | 215 | 245 |
| new-file-at-a-seam | 8 / 1 / 0 | 8 / 1 / 0 | 371 | 392 |
| new-subsystem-added | 6 / 3 / 0 | 6 / 3 / 0 | 180 | 312 |
| referenced-symbol-deleted | 10 / 0 / 0 | 10 / 0 / 0 | 66 | 55 |
| sibling-call-added | 9 / 1 / 0 | 8 / 2 / 0 | 194 | 156 |
| single-method-added | 10 / 0 / 0 | 10 / 0 / 0 | 159 | 185 |
| types-renamed-in-place | 7 / 2 / 0 | 7 / 2 / 0 | 325 | 408 |
| whole-module-deleted | 8 / 2 / 0 | 8 / 2 / 0 | 27 | 31 |
| wide-mechanical-rewrite | 9 / 0 / 0 | 9 / 0 / 0 | 76 | 60 |

base: 12 recorded runs; 105 criteria met; 11 violated; 0 execution errors.
head: 12 recorded runs; 104 criteria met; 12 violated; 0 execution errors.

## e2e

| Test | Base: met / violated / errors | PR: met / violated / errors | Base seconds | PR seconds |
|---|---:|---:|---:|---:|
| deleted-file-removes-its-component | 0 / 0 / 1 | 0 / 0 / 1 | 12 | 12 |
| deleted-method-leaves-its-component | 0 / 0 / 1 | 0 / 0 / 1 | 12 | 12 |
| extra-call-site-re-evidences-the-relation | 0 / 0 / 1 | 0 / 0 / 1 | 11 | 11 |
| last-sibling-is-absorbed-into-its-parent | 0 / 0 / 1 | 0 / 0 / 1 | 12 | 13 |
| new-call-adds-a-relation | 0 / 0 / 1 | 0 / 0 / 1 | 12 | 13 |
| new-file-gets-a-component | 0 / 0 / 1 | 0 / 0 / 1 | 12 | 12 |
| removed-call-removes-the-relation | 0 / 0 / 1 | 0 / 0 / 1 | 12 | 13 |

base: 7 recorded runs; 0 criteria met; 0 violated; 7 execution errors.
head: 7 recorded runs; 0 criteria met; 0 violated; 7 execution errors.

## incremental-repeat

| Test | Base: met / violated / errors | PR: met / violated / errors | Base seconds | PR seconds |
|---|---:|---:|---:|---:|
| sibling-call-added | 9 / 1 / 0 | 8 / 2 / 0 | 200 | 171 |

base: 1 recorded runs; 9 criteria met; 1 violated; 0 execution errors.
head: 1 recorded runs; 8 criteria met; 2 violated; 0 execution errors.

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

### release/base/codeboarding-pr626

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 149 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 149 file(s) analysed of 171 eligible (87%); 0 ignored-but-analysed, 22 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### release/head/codeboarding-pr626

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 149 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 149 file(s) analysed of 171 eligible (87%); 0 ignored-but-analysed, 22 missing, 0 not present at the commit.
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

### full/base/click-before-colorama-removal

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 16 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 16 file(s) analysed of 18 eligible (89%); 0 ignored-but-analysed, 2 missing, 0 not present at the commit.
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

### full/base/click-before-get-strerror-removal

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 15 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 15 file(s) analysed of 19 eligible (79%); 0 ignored-but-analysed, 4 missing, 0 not present at the commit.
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

### full/base/click-before-typing-modernisation

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 15 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 15 file(s) analysed of 17 eligible (88%); 0 ignored-but-analysed, 2 missing, 0 not present at the commit.
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

### full/base/codeboarding-before-health-checks

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 118 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 118 file(s) analysed of 133 eligible (89%); 0 ignored-but-analysed, 15 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/head/codeboarding-before-health-checks

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 118 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 118 file(s) analysed of 133 eligible (89%); 0 ignored-but-analysed, 15 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/base/codeboarding-before-local-server-removal

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 104 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 104 file(s) analysed of 113 eligible (92%); 0 ignored-but-analysed, 9 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/head/codeboarding-before-local-server-removal

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 104 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 104 file(s) analysed of 113 eligible (92%); 0 ignored-but-analysed, 9 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/base/codeboarding-before-model-env-removal

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 73 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 73 file(s) analysed of 79 eligible (92%); 0 ignored-but-analysed, 6 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/head/codeboarding-before-model-env-removal

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 73 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 73 file(s) analysed of 79 eligible (92%); 0 ignored-but-analysed, 6 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/base/gson-before-alternate-field-names

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 111 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 111 file(s) analysed of 116 eligible (96%); 0 ignored-but-analysed, 5 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/head/gson-before-alternate-field-names

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 111 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 111 file(s) analysed of 116 eligible (96%); 0 ignored-but-analysed, 5 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/base/gson-before-gson-types-rename

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 111 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 111 file(s) analysed of 116 eligible (96%); 0 ignored-but-analysed, 5 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/head/gson-before-gson-types-rename

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 111 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 111 file(s) analysed of 116 eligible (96%); 0 ignored-but-analysed, 5 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/base/gson-before-java-time-adapters

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 111 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 111 file(s) analysed of 116 eligible (96%); 0 ignored-but-analysed, 5 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/head/gson-before-java-time-adapters

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 111 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 111 file(s) analysed of 116 eligible (96%); 0 ignored-but-analysed, 5 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/base/gson-before-reflection-helper-move

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 109 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 109 file(s) analysed of 114 eligible (96%); 0 ignored-but-analysed, 5 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/head/gson-before-reflection-helper-move

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 109 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 109 file(s) analysed of 114 eligible (96%); 0 ignored-but-analysed, 5 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/base/mediatr-before-fsharp-assembly-support

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 79 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 79 file(s) analysed of 82 eligible (96%); 0 ignored-but-analysed, 3 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/head/mediatr-before-fsharp-assembly-support

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 79 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 79 file(s) analysed of 82 eligible (96%); 0 ignored-but-analysed, 3 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/base/serilog-before-restricted-sink-forwarding

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 109 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 109 file(s) analysed of 112 eligible (97%); 0 ignored-but-analysed, 3 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### full/head/serilog-before-restricted-sink-forwarding

- **met** `document_invariants`: all 27 invariants hold
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `no_phantom_files`: every one of the 109 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **reported** `source_coverage`: 109 file(s) analysed of 112 eligible (97%); 0 ignored-but-analysed, 3 missing, 0 not present at the commit.
- **skipped** `naming`: the judge did not run
- **skipped** `description_grounding`: the judge did not run

### incremental/base/body-edit-no-call-change

- **met** `structural_operations`: 11 expectation(s) met on the reading that held
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 1 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 2 architecture and 3 index edit(s) in all, 22 of 23 matched component(s) verbatim. The analysis's own description was rewritten.
- **reported** `drift_without_cause`: 1 component(s) changed content, 0 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `ungrounded_edge_churn`: every one of 0 backing edge change(s) has a code cause
- **reported** `relation_churn`: 1 relation(s) moved: 0 added, 0 removed, 1 re-worded
- **met** `no_phantom_files`: every one of the 73 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/head/body-edit-no-call-change

- **met** `structural_operations`: 11 expectation(s) met on the reading that held
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 1 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 1 architecture and 3 index edit(s) in all, 20 of 21 matched component(s) verbatim.
- **reported** `drift_without_cause`: 1 component(s) changed content, 0 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `ungrounded_edge_churn`: every one of 0 backing edge change(s) has a code cause
- **reported** `relation_churn`: 1 relation(s) moved: 0 added, 0 removed, 1 re-worded
- **met** `no_phantom_files`: every one of the 73 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/base/capability-retired-no-unit-lost

- **met** `structural_operations`: 9 expectation(s) met on the reading that held
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 1 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 4 architecture and 109 index edit(s) in all, 6 of 9 matched component(s) verbatim.
- **reported** `drift_without_cause`: 3 component(s) changed content, 1 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `ungrounded_edge_churn`: every one of 2 backing edge change(s) has a code cause
- **reported** `relation_churn`: 2 relation(s) moved: 0 added, 0 removed, 2 re-worded
- **met** `no_phantom_files`: every one of the 16 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/head/capability-retired-no-unit-lost

- **met** `structural_operations`: 9 expectation(s) met on the reading that held
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 1 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 4 architecture and 109 index edit(s) in all, 6 of 9 matched component(s) verbatim.
- **reported** `drift_without_cause`: 3 component(s) changed content, 1 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `ungrounded_edge_churn`: every one of 2 backing edge change(s) has a code cause
- **reported** `relation_churn`: 2 relation(s) moved: 0 added, 0 removed, 2 re-worded
- **met** `no_phantom_files`: every one of the 16 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/base/cross-boundary-call-added

- **met** `structural_operations`: 8 expectation(s) met on the reading that held
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 2 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 10 architecture and 45 index edit(s) in all, 17 of 22 matched component(s) verbatim.
- **reported** `drift_without_cause`: 4 component(s) changed content, 3 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **violated** `ungrounded_edge_churn`: 40 of 41 backing edge change(s) have both endpoint methods byte-identical
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Element<T>.write(JsonWriter) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Element<T>.write(JsonWriter) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.read(JsonReader)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.read(JsonReader)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> extras.src.main.java.com.google.gson.typeadapters.UtcDateTypeAdapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> test-shrinker.src.main.java.com.example.ClassWithAdapter.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> extras.src.main.java.com.google.gson.typeadapters.UtcDateTypeAdapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> test-shrinker.src.main.java.com.example.ClassWithAdapter.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> test-shrinker.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> test-shrinker.src.main.java.com.example.GenericClasses.DummyClass.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.write(JsonWriter, Object) -> extras.src.main.java.com.google.gson.typeadapters.UtcDateTypeAdapter.write(JsonWriter, Date)
- **reported** `relation_churn`: 5 relation(s) moved: 0 added, 0 removed, 5 re-worded
- **met** `no_phantom_files`: every one of the 111 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/head/cross-boundary-call-added

- **met** `structural_operations`: 8 expectation(s) met on the reading that held
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 2 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 11 architecture and 45 index edit(s) in all, 17 of 22 matched component(s) verbatim.
- **reported** `drift_without_cause`: 4 component(s) changed content, 3 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **violated** `ungrounded_edge_churn`: 40 of 41 backing edge change(s) have both endpoint methods byte-identical
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Element<T>.write(JsonWriter) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Element<T>.write(JsonWriter) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.read(JsonReader)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.read(JsonReader)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> extras.src.main.java.com.google.gson.typeadapters.UtcDateTypeAdapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> test-shrinker.src.main.java.com.example.ClassWithAdapter.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> test-shrinker.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> extras.src.main.java.com.google.gson.typeadapters.UtcDateTypeAdapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> test-shrinker.src.main.java.com.example.ClassWithAdapter.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> test-shrinker.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> test-shrinker.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.write(JsonWriter, Object) -> extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.write(JsonWriter, Object) -> extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.write(JsonWriter, T)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.write(JsonWriter, Object) -> extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.write(JsonWriter, Object) -> test-shrinker.src.main.java.com.example.ClassWithAdapter.Adapter.write(JsonWriter, ClassWithAdapter)
- **reported** `relation_churn`: 11 relation(s) moved: 0 added, 0 removed, 11 re-worded
- **met** `no_phantom_files`: every one of the 111 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/base/helpers-moved-between-classes

- **met** `structural_operations`: 8 expectation(s) met on the reading that held
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 4 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 8 architecture and 98 index edit(s) in all, 18 of 22 matched component(s) verbatim.
- **reported** `drift_without_cause`: 4 component(s) changed content, 4 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **violated** `ungrounded_edge_churn`: 55 of 69 backing edge change(s) have both endpoint methods byte-identical
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Element<T>.write(JsonWriter) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.read(JsonReader)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R) -> gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T)
  - gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> shrinker-test.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T) -> extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.write(JsonWriter, T)
  - gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T) -> extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.write(JsonWriter, T)
  - gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T) -> shrinker-test.src.main.java.com.example.ClassWithAdapter.Adapter.write(JsonWriter, ClassWithAdapter)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> extras.src.main.java.com.google.gson.typeadapters.UtcDateTypeAdapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> shrinker-test.src.main.java.com.example.ClassWithAdapter.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> shrinker-test.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> extras.src.main.java.com.google.gson.typeadapters.UtcDateTypeAdapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> shrinker-test.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.write(JsonWriter, Object) -> extras.src.main.java.com.google.gson.typeadapters.UtcDateTypeAdapter.write(JsonWriter, Date)
- **reported** `relation_churn`: 23 relation(s) moved: 0 added, 0 removed, 23 re-worded
- **met** `no_phantom_files`: every one of the 109 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/head/helpers-moved-between-classes

- **met** `structural_operations`: 8 expectation(s) met on the reading that held
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 4 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 11 architecture and 98 index edit(s) in all, 18 of 22 matched component(s) verbatim. The analysis's own description was rewritten.
- **reported** `drift_without_cause`: 4 component(s) changed content, 4 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **violated** `ungrounded_edge_churn`: 55 of 69 backing edge change(s) have both endpoint methods byte-identical
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Element<T>.write(JsonWriter) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R) -> gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.write(JsonWriter, T)
  - gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> extras.src.main.java.com.google.gson.typeadapters.UtcDateTypeAdapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> shrinker-test.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T) -> extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T)
  - gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T) -> extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R)
  - gson.src.main.java.com.google.gson.internal.Excluder.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T) -> shrinker-test.src.main.java.com.example.ClassWithAdapter.Adapter.write(JsonWriter, ClassWithAdapter)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> extras.src.main.java.com.google.gson.typeadapters.UtcDateTypeAdapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> shrinker-test.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> shrinker-test.src.main.java.com.example.GenericClasses.DummyClass.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> extras.src.main.java.com.google.gson.typeadapters.UtcDateTypeAdapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> shrinker-test.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.write(JsonWriter, Object) -> extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.write(JsonWriter, Object) -> extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R)
- **reported** `relation_churn`: 15 relation(s) moved: 0 added, 0 removed, 15 re-worded
- **met** `no_phantom_files`: every one of the 109 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/base/new-file-at-a-seam

- **met** `structural_operations`: 5 expectation(s) met on the reading that held
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **not_applicable** `anchor_fidelity`: this test names no symbol's fate, so there is nothing to check the document against
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 12 architecture and 232 index edit(s) in all, 17 of 22 matched component(s) verbatim.
- **reported** `drift_without_cause`: 4 component(s) changed content, 3 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **violated** `ungrounded_edge_churn`: 370 of 685 backing edge change(s) have both endpoint methods byte-identical
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Element<T>.write(JsonWriter) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.CLASS.new TypeAdapter() {...}.write(JsonWriter, Class)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Element<T>.write(JsonWriter) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.LONG.new TypeAdapter() {...}.write(JsonWriter, Number)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Element<T>.write(JsonWriter) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.STRING.new TypeAdapter() {...}.write(JsonWriter, String)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Element<T>.write(JsonWriter) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.URI.new TypeAdapter() {...}.write(JsonWriter, URI)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken) -> gson.src.main.java.com.google.gson.Gson.getAdapter(Class)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.URL.new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.ATOMIC_INTEGER.new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.CHARACTER.new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.URI.new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.STRING_BUILDER.new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.Gson.atomicLongArrayAdapter(TypeAdapter).new TypeAdapter() {...}.write(JsonWriter, AtomicLongArray)
  - extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.ATOMIC_INTEGER_ARRAY.new TypeAdapter() {...}.write(JsonWriter, AtomicIntegerArray)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.SHORT.new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.BYTE.new TypeAdapter() {...}.write(JsonWriter, Number)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.LONG.new TypeAdapter() {...}.write(JsonWriter, Number)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.STRING.new TypeAdapter() {...}.write(JsonWriter, String)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.STRING_BUFFER.new TypeAdapter() {...}.write(JsonWriter, StringBuffer)
  - gson.src.main.java.com.google.gson.Gson.FutureTypeAdapter<T>.write(JsonWriter, T) -> test-shrinker.src.main.java.com.example.ClassWithAdapter.Adapter.write(JsonWriter, ClassWithAdapter)
  - gson.src.main.java.com.google.gson.Gson.atomicLongAdapter(TypeAdapter).new TypeAdapter() {...}.write(JsonWriter, AtomicLong) -> extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R)
  - gson.src.main.java.com.google.gson.Gson.atomicLongAdapter(TypeAdapter).new TypeAdapter() {...}.write(JsonWriter, AtomicLong) -> test-shrinker.src.main.java.com.example.ClassWithAdapter.Adapter.write(JsonWriter, ClassWithAdapter)
  - gson.src.main.java.com.google.gson.Gson.fromJson(JsonReader, TypeToken) -> test-shrinker.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.Gson.getDelegateAdapter(TypeAdapterFactory, TypeToken) -> extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.create(Gson, TypeToken)
  - gson.src.main.java.com.google.gson.Gson.toJson(Object, Type, JsonWriter) -> extras.src.main.java.com.google.gson.typeadapters.UtcDateTypeAdapter.write(JsonWriter, Date)
  - gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.newTypeHierarchyFactory(Class, TypeAdapter).new TypeAdapterFactory() {...}.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> extras.src.main.java.com.google.gson.typeadapters.UtcDateTypeAdapter.read(JsonReader)
  - metrics.src.main.java.com.google.gson.metrics.BagOfPrimitivesDeserializationBenchmark.timeBagOfPrimitivesDefault(int) -> gson.src.main.java.com.google.gson.Gson.fromJson(String, Class)
- **reported** `relation_churn`: 25 relation(s) moved: 0 added, 0 removed, 25 re-worded
- **met** `no_phantom_files`: every one of the 113 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/head/new-file-at-a-seam

- **met** `structural_operations`: 5 expectation(s) met on the reading that held
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **not_applicable** `anchor_fidelity`: this test names no symbol's fate, so there is nothing to check the document against
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 13 architecture and 232 index edit(s) in all, 17 of 22 matched component(s) verbatim.
- **reported** `drift_without_cause`: 4 component(s) changed content, 3 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **violated** `ungrounded_edge_churn`: 370 of 685 backing edge change(s) have both endpoint methods byte-identical
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Element<T>.write(JsonWriter) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.BOOLEAN.new TypeAdapter() {...}.write(JsonWriter, Boolean)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Element<T>.write(JsonWriter) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.BYTE.new TypeAdapter() {...}.write(JsonWriter, Number)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Element<T>.write(JsonWriter) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.STRING_BUFFER.new TypeAdapter() {...}.write(JsonWriter, StringBuffer)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.Gson.atomicLongAdapter(TypeAdapter).new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.read(JsonReader) -> gson.src.main.java.com.google.gson.Gson.atomicLongAdapter(TypeAdapter).new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.CURRENCY.new TypeAdapter() {...}.write(JsonWriter, Currency)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.DOUBLE.new TypeAdapter() {...}.write(JsonWriter, Number)
  - extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader) -> gson.src.main.java.com.google.gson.Gson.doubleAdapter(boolean).new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.ATOMIC_INTEGER_ARRAY.new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.CLASS.new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.CURRENCY.new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.create(Gson, TypeToken) -> gson.src.main.java.com.google.gson.Gson.getDelegateAdapter(TypeAdapterFactory, TypeToken)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken) -> gson.src.main.java.com.google.gson.Gson.getDelegateAdapter(TypeAdapterFactory, TypeToken)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.newTypeHierarchyFactory(Class, TypeAdapter).new TypeAdapterFactory() {...}.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R) -> gson.src.main.java.com.google.gson.Gson.floatAdapter(boolean).new TypeAdapter() {...}.write(JsonWriter, Number)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.ATOMIC_INTEGER.new TypeAdapter() {...}.write(JsonWriter, AtomicInteger)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R) -> gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.BIT_SET.new TypeAdapter() {...}.write(JsonWriter, BitSet)
  - gson.src.main.java.com.google.gson.Gson.FutureTypeAdapter<T>.read(JsonReader) -> extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.Gson.atomicLongAdapter(TypeAdapter).new TypeAdapter() {...}.read(JsonReader) -> extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.Gson.atomicLongAdapter(TypeAdapter).new TypeAdapter() {...}.read(JsonReader) -> test-shrinker.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.Gson.atomicLongArrayAdapter(TypeAdapter).new TypeAdapter() {...}.read(JsonReader) -> extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.Gson.atomicLongArrayAdapter(TypeAdapter).new TypeAdapter() {...}.read(JsonReader) -> test-shrinker.src.main.java.com.example.ClassWithAdapter.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.Gson.fromJson(JsonReader, TypeToken) -> extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.TypeAdapters.newTypeHierarchyFactory(Class, TypeAdapter).new TypeAdapterFactory() {...}.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> test-shrinker.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Adapter.read(JsonReader)
  - metrics.src.main.java.com.google.gson.metrics.ParseBenchmark.GsonBindParser.parse(char[], Document) -> gson.src.main.java.com.google.gson.Gson.fromJson(Reader, TypeToken)
- **reported** `relation_churn`: 24 relation(s) moved: 0 added, 0 removed, 24 re-worded
- **met** `no_phantom_files`: every one of the 113 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/base/new-subsystem-added

- **violated** `structural_operations`: ll_component_added occurred 1 time(s) and this test's diff justifies none of it: 1.7 'Health Check Tests' — New in head at depth 2, owning 4 file(s).
  - ll_component_added occurred 1 time(s) and this test's diff justifies none of it: 1.7 'Health Check Tests' — New in head at depth 2, owning 4 file(s).
  - hl_component_added occurred 1 time(s) and this test's diff justifies none of it: 10 'Code Health Checks' — New in head at depth 1, owning 11 file(s).
- **violated** `blast_radius`: 4 component(s) changed content outside the blast radius the diff justifies: 1 'Test Suite', 1.2.1 'LSP Client and Analysis Results Tests', 1.6 'Diagram Analysis Tests', 1.7 'Health Check Tests'. The commit touches health/runner.py, health/config.py, health/models.py, health/checks/orphan_code.py, health/checks/god_class.py, health/checks/coupling.py, health/checks/inheritance.py, health/checks/cohesion.py, health/checks/function_size.py, health/checks/instability.py, health/checks/circular_deps.py, health/__init__.py, health/checks/__init__.py, health_main.py, diagram_analysis/diagram_generator.py, repo_utils/ignore.py, static_analyzer/lsp_client/client.py, static_analyzer/graph.py, repo_utils/__init__.py and nothing else.
  - 4 component(s) changed content outside the blast radius the diff justifies: 1 'Test Suite', 1.2.1 'LSP Client and Analysis Results Tests', 1.6 'Diagram Analysis Tests', 1.7 'Health Check Tests'. The commit touches health/runner.py, health/config.py, health/models.py, health/checks/orphan_code.py, health/checks/god_class.py, health/checks/coupling.py, health/checks/inheritance.py, health/checks/cohesion.py, health/checks/function_size.py, health/checks/instability.py, health/checks/circular_deps.py, health/__init__.py, health/checks/__init__.py, health_main.py, diagram_analysis/diagram_generator.py, repo_utils/ignore.py, static_analyzer/lsp_client/client.py, static_analyzer/graph.py, repo_utils/__init__.py and nothing else.
- **met** `file_delta`: nothing this test asks about went wrong
- **not_applicable** `anchor_fidelity`: this test names no symbol's fate, so there is nothing to check the document against
- **reported** `document_drift`: 8 architecture edit(s) on 5 component(s) the commit does not reach; 25 architecture and 256 index edit(s) in all, 18 of 28 matched component(s) verbatim. The analysis's own description was rewritten.
- **reported** `drift_without_cause`: 11 component(s) changed content, 5 kept a name while their content moved
- **violated** `introduced_invariants`: 1 invariant(s) violated that the baseline satisfied
  - hierarchy.cluster_ids_disjoint — Root components do not share source cluster ids — 11 findings, absent from the baseline
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `ungrounded_edge_churn`: every one of 340 backing edge change(s) has a code cause
- **reported** `relation_churn`: 14 relation(s) moved: 12 added, 0 removed, 2 re-worded
- **met** `no_phantom_files`: every one of the 134 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/head/new-subsystem-added

- **violated** `structural_operations`: ll_component_added occurred 4 time(s) and this test's diff justifies none of it: 1.7 'Health Check Tests' — New in head at depth 2, owning 4 file(s).; 10.1 'Code Health Checks' — New in head at depth 2, owning 8 file(s).; 10.2 'Health Check Orchestration and Models' — New in head at depth 2, owning 2 file(s).; 10.3 'Health Configuration Management' — New in head at depth 2, owning 1 file(s).
  - ll_component_added occurred 4 time(s) and this test's diff justifies none of it: 1.7 'Health Check Tests' — New in head at depth 2, owning 4 file(s).; 10.1 'Code Health Checks' — New in head at depth 2, owning 8 file(s).; 10.2 'Health Check Orchestration and Models' — New in head at depth 2, owning 2 file(s).; 10.3 'Health Configuration Management' — New in head at depth 2, owning 1 file(s).
  - hl_component_added occurred 1 time(s) and this test's diff justifies none of it: 10 'Architectural Health Checks' — New in head at depth 1, owning 11 file(s).
- **violated** `blast_radius`: 5 component(s) changed content outside the blast radius the diff justifies: 1 'Test Suite', 1.2.2 'LSP Client Tests', 1.2.3 'Analysis Cache and Structure Tests', 1.6 'Diagram Analysis Tests', 1.7 'Health Check Tests'. The commit touches health/runner.py, health/config.py, health/models.py, health/checks/orphan_code.py, health/checks/god_class.py, health/checks/coupling.py, health/checks/inheritance.py, health/checks/cohesion.py, health/checks/function_size.py, health/checks/instability.py, health/checks/circular_deps.py, health/__init__.py, health/checks/__init__.py, health_main.py, diagram_analysis/diagram_generator.py, repo_utils/ignore.py, static_analyzer/lsp_client/client.py, static_analyzer/graph.py, repo_utils/__init__.py and nothing else.
  - 5 component(s) changed content outside the blast radius the diff justifies: 1 'Test Suite', 1.2.2 'LSP Client Tests', 1.2.3 'Analysis Cache and Structure Tests', 1.6 'Diagram Analysis Tests', 1.7 'Health Check Tests'. The commit touches health/runner.py, health/config.py, health/models.py, health/checks/orphan_code.py, health/checks/god_class.py, health/checks/coupling.py, health/checks/inheritance.py, health/checks/cohesion.py, health/checks/function_size.py, health/checks/instability.py, health/checks/circular_deps.py, health/__init__.py, health/checks/__init__.py, health_main.py, diagram_analysis/diagram_generator.py, repo_utils/ignore.py, static_analyzer/lsp_client/client.py, static_analyzer/graph.py, repo_utils/__init__.py and nothing else.
- **met** `file_delta`: nothing this test asks about went wrong
- **not_applicable** `anchor_fidelity`: this test names no symbol's fate, so there is nothing to check the document against
- **reported** `document_drift`: 13 architecture edit(s) on 6 component(s) the commit does not reach; 28 architecture and 256 index edit(s) in all, 18 of 28 matched component(s) verbatim. The analysis's own description was rewritten.
- **reported** `drift_without_cause`: 13 component(s) changed content, 5 kept a name while their content moved
- **violated** `introduced_invariants`: 1 invariant(s) violated that the baseline satisfied
  - hierarchy.cluster_ids_disjoint — Root components do not share source cluster ids — 11 findings, absent from the baseline
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `ungrounded_edge_churn`: every one of 374 backing edge change(s) has a code cause
- **reported** `relation_churn`: 22 relation(s) moved: 20 added, 0 removed, 2 re-worded
- **met** `no_phantom_files`: every one of the 134 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/base/referenced-symbol-deleted

- **met** `structural_operations`: 8 expectation(s) met on the reading that held
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 3 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 3 architecture and 139 index edit(s) in all, 3 of 5 matched component(s) verbatim.
- **reported** `drift_without_cause`: 2 component(s) changed content, 1 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `ungrounded_edge_churn`: every one of 2 backing edge change(s) has a code cause
- **reported** `relation_churn`: 1 relation(s) moved: 0 added, 0 removed, 1 re-worded
- **met** `no_phantom_files`: every one of the 15 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/head/referenced-symbol-deleted

- **met** `structural_operations`: 8 expectation(s) met on the reading that held
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 3 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 3 architecture and 139 index edit(s) in all, 3 of 5 matched component(s) verbatim.
- **reported** `drift_without_cause`: 2 component(s) changed content, 1 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `ungrounded_edge_churn`: every one of 2 backing edge change(s) has a code cause
- **reported** `relation_churn`: 1 relation(s) moved: 0 added, 0 removed, 1 re-worded
- **met** `no_phantom_files`: every one of the 15 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/base/sibling-call-added

- **violated** `structural_operations`: edge_added is required by this test and did not occur. Observed: membership_gained
  - edge_added is required by this test and did not occur. Observed: membership_gained
  - edge_evidence_changed is required by this test and did not occur. Observed: membership_gained
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 4 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 9 architecture and 11 index edit(s) in all, 28 of 31 matched component(s) verbatim. The analysis's own description was rewritten.
- **reported** `drift_without_cause`: 3 component(s) changed content, 3 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `ungrounded_edge_churn`: every one of 0 backing edge change(s) has a code cause
- **reported** `relation_churn`: the edge set came through unchanged
- **met** `no_phantom_files`: every one of the 79 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/head/sibling-call-added

- **violated** `structural_operations`: edge_added is required by this test and did not occur. Observed: edge_removed, membership_gained
  - edge_added is required by this test and did not occur. Observed: edge_removed, membership_gained
  - edge_removed occurred 2 time(s) and this test's diff justifies none of it: 'Sender Contracts and Primitives' -> 'Request Contracts and Service Registration' — references request contracts; backing call sites 2; 'Mediator and Publisher Dispatcher' -> 'Request Contracts and Service Registration' — dispatches request contracts; backing call sites 2
  - edge_evidence_changed is required by this test and did not occur. Observed: edge_removed, membership_gained
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 4 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 9 architecture and 11 index edit(s) in all, 27 of 30 matched component(s) verbatim. The analysis's own description was rewritten.
- **reported** `drift_without_cause`: 3 component(s) changed content, 3 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **violated** `ungrounded_edge_churn`: 1 of 2 backing edge change(s) have both endpoint methods byte-identical
  - src.MediatR.ISender -> src.MediatR.Contracts.IRequest.IRequest
- **reported** `relation_churn`: 4 relation(s) moved: 0 added, 4 removed, 0 re-worded
- **met** `no_phantom_files`: every one of the 79 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/base/single-method-added

- **met** `structural_operations`: 9 expectation(s) met on the reading that held
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 1 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 7 architecture and 5 index edit(s) in all, 29 of 32 matched component(s) verbatim.
- **reported** `drift_without_cause`: 3 component(s) changed content, 3 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `ungrounded_edge_churn`: every one of 3 backing edge change(s) has a code cause
- **reported** `relation_churn`: 4 relation(s) moved: 0 added, 0 removed, 4 re-worded
- **met** `no_phantom_files`: every one of the 109 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/head/single-method-added

- **met** `structural_operations`: 9 expectation(s) met on the reading that held
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 1 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 7 architecture and 5 index edit(s) in all, 29 of 32 matched component(s) verbatim.
- **reported** `drift_without_cause`: 3 component(s) changed content, 3 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `ungrounded_edge_churn`: every one of 3 backing edge change(s) has a code cause
- **reported** `relation_churn`: 4 relation(s) moved: 0 added, 0 removed, 4 re-worded
- **met** `no_phantom_files`: every one of the 109 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/base/types-renamed-in-place

- **violated** `structural_operations`: edge_evidence_changed occurred 16 time(s) and this test's diff justifies none of it: 'Configuration Builder and Metadata Annotations' -> 'Internal Type Adapters and Reflection Infrastructure' — relabelled configures internal excluders and type factories -> configures exclusion strategies and date adapters; 'Configuration Builder and Metadata Annotations' -> 'Internal Runtime and Reflection Utilities' — relabelled configures internal excluders and type factories -> configures exclusion strategies and date adapters; 'Shrinking Test Harness and Core Fixtures' -> 'Configuration Builder and Metadata Annotations' — relabelled consumes tokens from stream readers -> consumes reader APIs from; backing call sites 52 -> 26; 'Shrinking Test Harness and Core Fixtures' -> 'Gson Runtime and Type Adapter Engine' — relabelled consumes tokens from stream readers -> consumes reader APIs from; backing call sites 19 -> 16; 'Unannotated Class Shrinking Runner' -> 'Configuration Builder and Metadata Annotations' — relabelled consumes tokens from stream readers -> consumes reader APIs from; backing call sites 4 -> 2
  - edge_evidence_changed occurred 16 time(s) and this test's diff justifies none of it: 'Configuration Builder and Metadata Annotations' -> 'Internal Type Adapters and Reflection Infrastructure' — relabelled configures internal excluders and type factories -> configures exclusion strategies and date adapters; 'Configuration Builder and Metadata Annotations' -> 'Internal Runtime and Reflection Utilities' — relabelled configures internal excluders and type factories -> configures exclusion strategies and date adapters; 'Shrinking Test Harness and Core Fixtures' -> 'Configuration Builder and Metadata Annotations' — relabelled consumes tokens from stream readers -> consumes reader APIs from; backing call sites 52 -> 26; 'Shrinking Test Harness and Core Fixtures' -> 'Gson Runtime and Type Adapter Engine' — relabelled consumes tokens from stream readers -> consumes reader APIs from; backing call sites 19 -> 16; 'Unannotated Class Shrinking Runner' -> 'Configuration Builder and Metadata Annotations' — relabelled consumes tokens from stream readers -> consumes reader APIs from; backing call sites 4 -> 2
  - edge_removed occurred 5 time(s) and this test's diff justifies none of it: 'Internal Type Adapters and Reflection Infrastructure' -> 'Protocol Buffers Type Adapter' — delegates deserialization to protobuf adapter; backing call sites 2; 'Protocol Buffers Type Adapter' -> 'Internal Type Adapters and Reflection Infrastructure' — interacts with tree adapter context; backing call sites 2; 'Protocol Buffers Type Adapter' -> 'Built-in Type Adapters and Binding Factories' — interacts with tree adapter context; backing call sites 2; 'Advanced Adapters and Extensions' -> 'Configuration Builder and Metadata Annotations' — implements and wraps core adapters; backing call sites 3; 'Built-in Type Adapters and Binding Factories' -> 'Protocol Buffers Type Adapter' — delegates deserialization to protobuf adapter; backing call sites 2
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **not_applicable** `anchor_fidelity`: this test names no symbol's fate, so there is nothing to check the document against
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 13 architecture and 140 index edit(s) in all, 16 of 22 matched component(s) verbatim.
- **reported** `drift_without_cause`: 6 component(s) changed content, 3 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **violated** `ungrounded_edge_churn`: 182 of 266 backing edge change(s) have both endpoint methods byte-identical
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Element<T>.write(JsonWriter) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.TreeTypeAdapter<T>.read(JsonReader)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.addType(Type) -> gson.src.main.java.com.google.gson.internal.ConstructorConstructor.get(TypeToken)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.Adapter<T, A>.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.write(JsonWriter, T) -> gson.src.main.java.com.google.gson.internal.bind.ArrayTypeAdapter<E>.write(JsonWriter, Object)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken) -> gson.src.main.java.com.google.gson.reflect.TypeToken<T>.getRawType()
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.TreeTypeAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ArrayTypeAdapter<E>.read(JsonReader) -> test-shrinker.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ArrayTypeAdapter<E>.read(JsonReader) -> test-shrinker.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ArrayTypeAdapter<E>.read(JsonReader) -> test-shrinker.src.main.java.com.example.GenericClasses.DummyClass.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ArrayTypeAdapter<E>.write(JsonWriter, Object) -> extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R)
  - gson.src.main.java.com.google.gson.internal.bind.CollectionTypeAdapterFactory.Adapter<E>.write(JsonWriter, Collection) -> test-shrinker.src.main.java.com.example.ClassWithAdapter.Adapter.write(JsonWriter, ClassWithAdapter)
  - gson.src.main.java.com.google.gson.internal.bind.MapTypeAdapterFactory.Adapter<K, V>.read(JsonReader) -> extras.src.main.java.com.google.gson.typeadapters.PostConstructAdapterFactory.PostConstructAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.MapTypeAdapterFactory.Adapter<K, V>.write(JsonWriter, Map) -> extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.write(JsonWriter, T)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.TreeTypeAdapter<T>.read(JsonReader) -> extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.TreeTypeAdapter<T>.read(JsonReader) -> extras.src.main.java.com.google.gson.typeadapters.UtcDateTypeAdapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.TreeTypeAdapter<T>.read(JsonReader) -> test-shrinker.src.main.java.com.example.ClassWithAdapter.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.TreeTypeAdapter<T>.read(JsonReader) -> test-shrinker.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Deserializer.deserialize(JsonElement, Type, JsonDeserializationContext)
  - metrics.src.main.java.com.google.gson.metrics.ParseBenchmark.GsonBindParser -> gson.src.main.java.com.google.gson.GsonBuilder.setDateFormat(String)
  - test-shrinker.src.main.java.com.example.Main.testSerializedName(BiConsumer) -> gson.src.main.java.com.google.gson.GsonBuilder.create()
  - test-shrinker.src.main.java.com.example.Main.testSerializedName(BiConsumer) -> gson.src.main.java.com.google.gson.GsonBuilder.setPrettyPrinting()
  - test-shrinker.src.main.java.com.example.Main.testUnreferencedConstructorNoArgs(BiConsumer) -> gson.src.main.java.com.google.gson.GsonBuilder.create()
- **reported** `relation_churn`: 22 relation(s) moved: 0 added, 5 removed, 17 re-worded
- **met** `no_phantom_files`: every one of the 111 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/head/types-renamed-in-place

- **violated** `structural_operations`: edge_evidence_changed occurred 16 time(s) and this test's diff justifies none of it: 'Builder Configuration and Annotations' -> 'Internal Type Adapters and Utilities' — relabelled configures internal excluders and type adapters -> configures internal excluder and type adapters; 'Builder Configuration and Annotations' -> 'Internal Reflection and Object Construction' — relabelled configures internal excluders and type adapters -> configures internal excluder and type adapters; 'Shrinker Test Runner and Core Fixtures' -> 'Builder Configuration and Annotations' — relabelled consumes JSON streaming parser APIs -> reads stream tokens via core streaming APIs; backing call sites 52 -> 26; 'Shrinker Test Runner and Core Fixtures' -> 'Gson Facade and Type Adapters' — relabelled consumes JSON streaming parser APIs -> reads stream tokens via core streaming APIs; backing call sites 19 -> 16; 'Unannotated Class Test Runner and Fixtures' -> 'Builder Configuration and Annotations' — relabelled consumes JSON streaming parser APIs -> reads stream tokens via core streaming APIs; backing call sites 4 -> 2
  - edge_evidence_changed occurred 16 time(s) and this test's diff justifies none of it: 'Builder Configuration and Annotations' -> 'Internal Type Adapters and Utilities' — relabelled configures internal excluders and type adapters -> configures internal excluder and type adapters; 'Builder Configuration and Annotations' -> 'Internal Reflection and Object Construction' — relabelled configures internal excluders and type adapters -> configures internal excluder and type adapters; 'Shrinker Test Runner and Core Fixtures' -> 'Builder Configuration and Annotations' — relabelled consumes JSON streaming parser APIs -> reads stream tokens via core streaming APIs; backing call sites 52 -> 26; 'Shrinker Test Runner and Core Fixtures' -> 'Gson Facade and Type Adapters' — relabelled consumes JSON streaming parser APIs -> reads stream tokens via core streaming APIs; backing call sites 19 -> 16; 'Unannotated Class Test Runner and Fixtures' -> 'Builder Configuration and Annotations' — relabelled consumes JSON streaming parser APIs -> reads stream tokens via core streaming APIs; backing call sites 4 -> 2
  - edge_removed occurred 5 time(s) and this test's diff justifies none of it: 'Internal Type Adapters and Utilities' -> 'Protocol Buffers Type Adapter' — delegates serialization to protobuf adapter; backing call sites 2; 'Protocol Buffers Type Adapter' -> 'Internal Type Adapters and Utilities' — invokes tree adapter context for nested deserialization; backing call sites 2; 'Protocol Buffers Type Adapter' -> 'Built-in Type Adapters and Tree Binding' — invokes tree adapter context for nested deserialization; backing call sites 2; 'Type Adapter Extensions' -> 'Builder Configuration and Annotations' — extends and invokes core TypeAdapter APIs; backing call sites 3; 'Built-in Type Adapters and Tree Binding' -> 'Protocol Buffers Type Adapter' — delegates serialization to protobuf adapter; backing call sites 2
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **not_applicable** `anchor_fidelity`: this test names no symbol's fate, so there is nothing to check the document against
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 11 architecture and 140 index edit(s) in all, 16 of 22 matched component(s) verbatim.
- **reported** `drift_without_cause`: 6 component(s) changed content, 3 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **violated** `ungrounded_edge_churn`: 182 of 266 backing edge change(s) have both endpoint methods byte-identical
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Element<T>.write(JsonWriter) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken) -> gson.src.main.java.com.google.gson.reflect.TypeToken<T>.getType()
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.MapTypeAdapterFactory.Adapter<K, V>.read(JsonReader)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.GraphAdapterBuilder() -> gson.src.main.java.com.google.gson.internal.ConstructorConstructor.ConstructorConstructor(Map, boolean, List)
  - extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.addType(Type) -> gson.src.main.java.com.google.gson.reflect.TypeToken<T>.get(Type)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.InterceptorAdapter<T>.read(JsonReader) -> gson.src.main.java.com.google.gson.internal.bind.MapTypeAdapterFactory.Adapter<K, V>.read(JsonReader)
  - extras.src.main.java.com.google.gson.interceptors.InterceptorFactory.create(Gson, TypeToken) -> gson.src.main.java.com.google.gson.reflect.TypeToken<T>.getRawType()
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken) -> gson.src.main.java.com.google.gson.reflect.TypeToken<T>.get(Class)
  - extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R) -> gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, T)
  - gson.src.main.java.com.google.gson.internal.ConstructorConstructor.get(TypeToken, boolean) -> extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.addType(Type).new InstanceCreator() {...}.createInstance(Type)
  - gson.src.main.java.com.google.gson.internal.bind.ArrayTypeAdapter<E>.read(JsonReader) -> test-shrinker.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ArrayTypeAdapter<E>.write(JsonWriter, Object) -> extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.write(JsonWriter, R)
  - gson.src.main.java.com.google.gson.internal.bind.CollectionTypeAdapterFactory.Adapter<E>.read(JsonReader) -> extras.src.main.java.com.google.gson.typeadapters.RuntimeTypeAdapterFactory<T>.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.CollectionTypeAdapterFactory.Adapter<E>.read(JsonReader) -> test-shrinker.src.main.java.com.example.ClassWithAdapter.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.CollectionTypeAdapterFactory.Adapter<E>.write(JsonWriter, Collection) -> test-shrinker.src.main.java.com.example.ClassWithAdapter.Adapter.write(JsonWriter, ClassWithAdapter)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoArray(JsonReader, int, Object[]) -> test-shrinker.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Adapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> extras.src.main.java.com.google.gson.graph.GraphAdapterBuilder.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.readIntoField(JsonReader, Object) -> test-shrinker.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Factory.create(Gson, TypeToken).new TypeAdapter() {...}.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.ReflectiveTypeAdapterFactory.createBoundField(Gson, Field, Method, String, TypeToken, boolean, boolean).new BoundField() {...}.write(JsonWriter, Object) -> test-shrinker.src.main.java.com.example.ClassWithAdapter.Adapter.write(JsonWriter, ClassWithAdapter)
  - gson.src.main.java.com.google.gson.internal.bind.TreeTypeAdapter<T>.read(JsonReader) -> extras.src.main.java.com.google.gson.typeadapters.UtcDateTypeAdapter.read(JsonReader)
  - gson.src.main.java.com.google.gson.internal.bind.TreeTypeAdapter<T>.read(JsonReader) -> test-shrinker.src.main.java.com.example.ClassWithJsonAdapterAnnotation.Adapter.read(JsonReader)
  - metrics.src.main.java.com.google.gson.metrics.ParseBenchmark.GsonBindParser -> gson.src.main.java.com.google.gson.GsonBuilder.setDateFormat(String)
  - proto.src.main.java.com.google.gson.protobuf.ProtoTypeAdapter.deserialize(JsonElement, Type, JsonDeserializationContext) -> gson.src.main.java.com.google.gson.internal.bind.TreeTypeAdapter<T>.GsonContextImpl.deserialize(JsonElement, Type)
  - test-shrinker.src.main.java.com.example.Main.testNoJdkUnsafe(BiConsumer) -> gson.src.main.java.com.google.gson.GsonBuilder.create()
  - test-shrinker.src.main.java.com.example.Main.testUnreferencedConstructorNoArgs(BiConsumer) -> gson.src.main.java.com.google.gson.GsonBuilder.setPrettyPrinting()
- **reported** `relation_churn`: 22 relation(s) moved: 0 added, 5 removed, 17 re-worded
- **met** `no_phantom_files`: every one of the 111 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/base/whole-module-deleted

- **violated** `structural_operations`: ll_component_removed is required by this test and did not occur. Observed: membership_lost
  - ll_component_removed is required by this test and did not occur. Observed: membership_lost
  - edge_removed is required by this test and did not occur. Observed: membership_lost
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 4 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 3 architecture and 13 index edit(s) in all, 28 of 29 matched component(s) verbatim.
- **reported** `drift_without_cause`: 1 component(s) changed content, 1 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `ungrounded_edge_churn`: every one of 0 backing edge change(s) has a code cause
- **reported** `relation_churn`: the edge set came through unchanged
- **violated** `no_phantom_files`: 1 file(s) described but not present at this commit
  - local_app.py
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/head/whole-module-deleted

- **violated** `structural_operations`: ll_component_removed is required by this test and did not occur. Observed: membership_lost
  - ll_component_removed is required by this test and did not occur. Observed: membership_lost
  - edge_removed is required by this test and did not occur. Observed: membership_lost
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 4 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 2 architecture and 13 index edit(s) in all, 28 of 29 matched component(s) verbatim.
- **reported** `drift_without_cause`: 1 component(s) changed content, 1 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `ungrounded_edge_churn`: every one of 0 backing edge change(s) has a code cause
- **reported** `relation_churn`: the edge set came through unchanged
- **violated** `no_phantom_files`: 1 file(s) described but not present at this commit
  - local_app.py
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/base/wide-mechanical-rewrite

- **met** `structural_operations`: 11 expectation(s) met on the reading that held
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **not_applicable** `anchor_fidelity`: this test names no symbol's fate, so there is nothing to check the document against
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 18 architecture and 504 index edit(s) in all, 0 of 9 matched component(s) verbatim. The analysis's own description was rewritten.
- **reported** `drift_without_cause`: 9 component(s) changed content, 0 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `ungrounded_edge_churn`: every one of 0 backing edge change(s) has a code cause
- **reported** `relation_churn`: the edge set came through unchanged
- **met** `no_phantom_files`: every one of the 15 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental/head/wide-mechanical-rewrite

- **met** `structural_operations`: 11 expectation(s) met on the reading that held
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **not_applicable** `anchor_fidelity`: this test names no symbol's fate, so there is nothing to check the document against
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 11 architecture and 504 index edit(s) in all, 0 of 9 matched component(s) verbatim.
- **reported** `drift_without_cause`: 9 component(s) changed content, 0 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `ungrounded_edge_churn`: every one of 0 backing edge change(s) has a code cause
- **reported** `relation_churn`: the edge set came through unchanged
- **met** `no_phantom_files`: every one of the 15 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### e2e/base/deleted-file-removes-its-component

**Execution error:** EngineError: The engine's run_incremental failed: run_incremental raised StaticAnalysisFatalError: /tmp/codeboarding-evals-engine-ckkluass/incremental/static_analysis.pkl was written by a different engine version and cannot be reused for an incremental run. Re-run a full analysis to rebuild it

- **not_applicable** `no_phantom_files`: this test produced no analysis
- **not_applicable** `expected_changes`: this test produced no analysis

### e2e/head/deleted-file-removes-its-component

**Execution error:** EngineError: The engine's run_incremental failed: run_incremental raised StaticAnalysisFatalError: /tmp/codeboarding-evals-engine-on8q3sq9/incremental/static_analysis.pkl was written by a different engine version and cannot be reused for an incremental run. Re-run a full analysis to rebuild it

- **not_applicable** `no_phantom_files`: this test produced no analysis
- **not_applicable** `expected_changes`: this test produced no analysis

### e2e/base/deleted-method-leaves-its-component

**Execution error:** EngineError: The engine's run_incremental failed: run_incremental raised StaticAnalysisFatalError: /tmp/codeboarding-evals-engine-a5_zj1ck/incremental/static_analysis.pkl was written by a different engine version and cannot be reused for an incremental run. Re-run a full analysis to rebuild it

- **not_applicable** `no_phantom_files`: this test produced no analysis
- **not_applicable** `expected_changes`: this test produced no analysis

### e2e/head/deleted-method-leaves-its-component

**Execution error:** EngineError: The engine's run_incremental failed: run_incremental raised StaticAnalysisFatalError: /tmp/codeboarding-evals-engine-bm6cmnea/incremental/static_analysis.pkl was written by a different engine version and cannot be reused for an incremental run. Re-run a full analysis to rebuild it

- **not_applicable** `no_phantom_files`: this test produced no analysis
- **not_applicable** `expected_changes`: this test produced no analysis

### e2e/base/extra-call-site-re-evidences-the-relation

**Execution error:** EngineError: The engine's run_incremental failed: run_incremental raised StaticAnalysisFatalError: /tmp/codeboarding-evals-engine-s6635eq4/incremental/static_analysis.pkl was written by a different engine version and cannot be reused for an incremental run. Re-run a full analysis to rebuild it

- **not_applicable** `no_phantom_files`: this test produced no analysis
- **not_applicable** `expected_changes`: this test produced no analysis

### e2e/head/extra-call-site-re-evidences-the-relation

**Execution error:** EngineError: The engine's run_incremental failed: run_incremental raised StaticAnalysisFatalError: /tmp/codeboarding-evals-engine-v543c9t1/incremental/static_analysis.pkl was written by a different engine version and cannot be reused for an incremental run. Re-run a full analysis to rebuild it

- **not_applicable** `no_phantom_files`: this test produced no analysis
- **not_applicable** `expected_changes`: this test produced no analysis

### e2e/base/last-sibling-is-absorbed-into-its-parent

**Execution error:** EngineError: The engine's run_incremental failed: run_incremental raised StaticAnalysisFatalError: /tmp/codeboarding-evals-engine-0216pexi/incremental/static_analysis.pkl was written by a different engine version and cannot be reused for an incremental run. Re-run a full analysis to rebuild it

- **not_applicable** `no_phantom_files`: this test produced no analysis
- **not_applicable** `expected_changes`: this test produced no analysis

### e2e/head/last-sibling-is-absorbed-into-its-parent

**Execution error:** EngineError: The engine's run_incremental failed: run_incremental raised StaticAnalysisFatalError: /tmp/codeboarding-evals-engine-u3x2ko2w/incremental/static_analysis.pkl was written by a different engine version and cannot be reused for an incremental run. Re-run a full analysis to rebuild it

- **not_applicable** `no_phantom_files`: this test produced no analysis
- **not_applicable** `expected_changes`: this test produced no analysis

### e2e/base/new-call-adds-a-relation

**Execution error:** EngineError: The engine's run_incremental failed: run_incremental raised StaticAnalysisFatalError: /tmp/codeboarding-evals-engine-t70o8h_d/incremental/static_analysis.pkl was written by a different engine version and cannot be reused for an incremental run. Re-run a full analysis to rebuild it

- **not_applicable** `no_phantom_files`: this test produced no analysis
- **not_applicable** `expected_changes`: this test produced no analysis

### e2e/head/new-call-adds-a-relation

**Execution error:** EngineError: The engine's run_incremental failed: run_incremental raised StaticAnalysisFatalError: /tmp/codeboarding-evals-engine-7z3vbyau/incremental/static_analysis.pkl was written by a different engine version and cannot be reused for an incremental run. Re-run a full analysis to rebuild it

- **not_applicable** `no_phantom_files`: this test produced no analysis
- **not_applicable** `expected_changes`: this test produced no analysis

### e2e/base/new-file-gets-a-component

**Execution error:** EngineError: The engine's run_incremental failed: run_incremental raised StaticAnalysisFatalError: /tmp/codeboarding-evals-engine-i26eq0qh/incremental/static_analysis.pkl was written by a different engine version and cannot be reused for an incremental run. Re-run a full analysis to rebuild it

- **not_applicable** `no_phantom_files`: this test produced no analysis
- **not_applicable** `expected_changes`: this test produced no analysis

### e2e/head/new-file-gets-a-component

**Execution error:** EngineError: The engine's run_incremental failed: run_incremental raised StaticAnalysisFatalError: /tmp/codeboarding-evals-engine-utwvu5j1/incremental/static_analysis.pkl was written by a different engine version and cannot be reused for an incremental run. Re-run a full analysis to rebuild it

- **not_applicable** `no_phantom_files`: this test produced no analysis
- **not_applicable** `expected_changes`: this test produced no analysis

### e2e/base/removed-call-removes-the-relation

**Execution error:** EngineError: The engine's run_incremental failed: run_incremental raised StaticAnalysisFatalError: /tmp/codeboarding-evals-engine-n309cwc5/incremental/static_analysis.pkl was written by a different engine version and cannot be reused for an incremental run. Re-run a full analysis to rebuild it

- **not_applicable** `no_phantom_files`: this test produced no analysis
- **not_applicable** `expected_changes`: this test produced no analysis

### e2e/head/removed-call-removes-the-relation

**Execution error:** EngineError: The engine's run_incremental failed: run_incremental raised StaticAnalysisFatalError: /tmp/codeboarding-evals-engine-u0kr1s6m/incremental/static_analysis.pkl was written by a different engine version and cannot be reused for an incremental run. Re-run a full analysis to rebuild it

- **not_applicable** `no_phantom_files`: this test produced no analysis
- **not_applicable** `expected_changes`: this test produced no analysis

### incremental-repeat/base/sibling-call-added

- **violated** `structural_operations`: edge_added is required by this test and did not occur. Observed: membership_gained
  - edge_added is required by this test and did not occur. Observed: membership_gained
  - edge_evidence_changed is required by this test and did not occur. Observed: membership_gained
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 4 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 10 architecture and 11 index edit(s) in all, 28 of 31 matched component(s) verbatim. The analysis's own description was rewritten.
- **reported** `drift_without_cause`: 3 component(s) changed content, 3 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **met** `ungrounded_edge_churn`: every one of 0 backing edge change(s) has a code cause
- **reported** `relation_churn`: the edge set came through unchanged
- **met** `no_phantom_files`: every one of the 79 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

### incremental-repeat/head/sibling-call-added

- **violated** `structural_operations`: edge_added is required by this test and did not occur. Observed: edge_removed, membership_gained
  - edge_added is required by this test and did not occur. Observed: edge_removed, membership_gained
  - edge_removed occurred 2 time(s) and this test's diff justifies none of it: 'Sender Contracts and Primitives' -> 'Request Contracts and Service Registration' — references request contracts; backing call sites 2; 'Mediator and Publisher Dispatcher' -> 'Request Contracts and Service Registration' — dispatches request contracts; backing call sites 2
  - edge_evidence_changed is required by this test and did not occur. Observed: edge_removed, membership_gained
- **met** `blast_radius`: nothing this test asks about went wrong
- **met** `file_delta`: nothing this test asks about went wrong
- **met** `anchor_fidelity`: all 4 decidable symbol(s) show the fate the commit gives them
- **reported** `document_drift`: 0 architecture edit(s) on 0 component(s) the commit does not reach; 9 architecture and 11 index edit(s) in all, 27 of 30 matched component(s) verbatim. The analysis's own description was rewritten.
- **reported** `drift_without_cause`: 3 component(s) changed content, 3 kept a name while their content moved
- **met** `introduced_invariants`: nothing introduced, and the baseline was clean
- **met** `tree_shape`: every expanded component splits into more than one sub-component
- **met** `edge_grounding`: every backing edge is grounded
- **violated** `ungrounded_edge_churn`: 1 of 2 backing edge change(s) have both endpoint methods byte-identical
  - src.MediatR.ISender -> src.MediatR.Contracts.IRequest.IRequest
- **reported** `relation_churn`: 4 relation(s) moved: 0 added, 4 removed, 0 re-worded
- **met** `no_phantom_files`: every one of the 79 described file(s) exists here
- **met** `no_ignored_files`: nothing the repository excludes is described
- **skipped** `architectural_drift`: the judge did not run
- **skipped** `edge_changes`: the judge did not run

