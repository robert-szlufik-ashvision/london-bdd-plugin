---
name: review
description: Slice-level review gate — reviewer agent audits the whole slice diff, clean code pass, distil-candidate triage, then close the outer loop. Runs when the collaborator queue is empty.
---

# /review — Close the Slice Properly

## Preconditions

`.bdd/session.yml`: `phase: close`, all collaborators `done`, on
feature/<slice>-tmp.

## Steps

1. **Wire the composition root.** Real implementations composed at the
   boundary; no test doubles leak into production wiring.
2. **Run the outer net.**
   - GREENFIELD: acceptance test → expect `🟢 AT`. Still red → treat as a
     new inner-loop entry point (missing wiring is the usual cause);
     re-open `phase: inner` with the discovered collaborator.
   - SAFE/FULL_REFACTOR: re-run the declared net (`net.suites` in
     session.yml). For a characterisation net, that's `/characterise` in
     verification mode; for `net.kind: existing`, just run the declared
     suites and show the output. Either way: green = behaviour preserved;
     red = STOP and present the diff to the user — intended change or
     regression is never the agent's call alone (auto: hand back).
3. **Dispatch `london-bdd:reviewer`** with the full slice diff
   (feature/<slice>-tmp vs base). Slice-level concerns: duplication across cycles,
   names drifting from the ubiquitous language, boundary rule violations,
   test suite coherence, anything implemented beyond what tests demand.
   Supervised: present proposals; apply accepted ones. Auto: apply as in
   `/inner` step 3 (block + should, re-review at most twice, then hand
   back). Keep everything green.
4. **Distil triage.** Walk `net.distil_candidates` (characterisation nets
   only; an existing-suite net has none): for each, decide with the user —
   rewrite now as a proper unit/acceptance test (then delete the ugly
   one), or log to `/debt` with the insight gained. The characterisation
   suite should shrink over time. Auto: you may write the distilled
   replacement, but never delete the ugly original (hook-enforced) — log
   each to `/debt` as "distilled, ready to delete" for the developer.
5. **Reflection (from the skill):** any awkward mock (design smell)?
   duplicated setup? happy AND sad path covered?
6. Final commit on feature/<slice>-tmp. Set `phase: done` (auto: in the
   same edit record `review: {scope: slice, blocking: 0}` — the guard
   requires it to enter `done`).
7. Tell the user (auto: this is where the unattended run ends — summarise
   the cycles, the decisions you recorded, and any `/debt` entries):
   the slice is closed, next command is `/commit-merge` to
   land it on feature/<slice>-main (manual by default, `--auto` to let the
   agent run the squash-merge itself).
