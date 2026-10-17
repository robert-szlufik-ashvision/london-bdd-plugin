---
name: decompose
description: Break the slice into collaborators — name roles in DDD language, sketch interface boundaries, agree the sequence before any code. Runs between the outer red and the inner loop.
---

# /decompose — Discover Collaborators, Name Roles

Read the `london-bdd` skill and `references/collaborator-discovery.md`.

## Preconditions

1. `.bdd/session.yml`: `phase: decompose`, outer net red (AT) or green
   (characterisation), on feature/<slice>-tmp.

## Steps

1. Starting from the entry point, ask per responsibility: *what does this
   object need to ask for or tell?* Name each discovered role by
   **capability, not implementation** (`OrderRepository`, not
   `SqliteOrderStore`), in the project's ubiquitous language.
2. **Role names are design decisions.**
   - Supervised: confirm every role name with the user before recording it.
   - Auto: name it yourself, strictly in the ubiquitous language agreed at
     `/scenario` (and `docs/glossary.md` if present). Record a one-line
     rationale per role in the map. A role that needs a term the agreed
     language doesn't have → hand back (skill: Autonomy).
3. Sketch the minimal interface per role: only the methods actually
   called, starting with one.
4. Sanity checks (from the skill): >3–4 collaborators on one class →
   challenge; chain deeper than 3 levels → slice thinner.
5. Order the queue outside-in (entry point first) and write it to
   `collaborators:` in session.yml, all `status: pending`.
   Set `phase: inner`.
6. Commit: `docs: collaborator map — <slice>` (the map goes in
   `.bdd/scope-<slice>.md` or the session file — no production code).
7. Supervised: tell the user the next command is `/inner`, which works
   the queue one collaborator at a time. Auto: run `/inner` now.

## Rules

- No production code, no interfaces committed as code yet — the inner loop
  drives them into existence. This is a map, not a skeleton.
- In FULL_REFACTOR mode, the map must respect the Clean Architecture
  boundary rule in the skill (UI knows nothing of domain; domain depends
  on nothing).
