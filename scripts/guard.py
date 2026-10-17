#!/usr/bin/env python3
"""london-bdd guard hook.

Enforces the four hard rules when a .bdd/session.yml slice is active:

  A. No production edit without a license: a recorded failing test, phase
     `close`, or SAFE mode with a green net. The net is declared in
     session.yml as a `net:` block (kind: existing — trusted suites the
     scope is already covered by — or kind: characterisation); a legacy
     `characterisation:` block still counts as a characterisation-kind
     net. The guard checks status, not kind: which suites constitute the
     net is the human's declaration at scope time. The implementer
     subagent (identified by the hook payload's agent_type) may write
     production code only — never tests, .bdd/ or docs. Under
     `autonomy: auto` the recorded failing test must exist (file on disk,
     name in the file) — no human is watching the red.
  B. In SAFE / FULL_REFACTOR mode, no edit outside the declared scope.
  C. `git commit` only on the slice's feature/<slice>-tmp branch. Merges
     onto feature/<slice>-main are the developer's manual action, unless
     `/commit-merge --auto` has dropped a fresh, untracked
     `.bdd/.merge-authorized` marker naming this exact tmp/base branch
     pair (see find_merge_marker). Rebases are never allowed for the agent.
     Under `autonomy: auto` a commit may not delete test files, and a
     cycle commit must carry a clean recorded review for that cycle plus
     a test change (see auto_commit_violation).
  D. Phase transitions in session.yml are forward-only and earned (see
     validate_phase_transition) — checked on Edit/Write/MultiEdit, and on
     Bash only for the narrow case of a redirect/tee write at that exact
     path (see bash_targets_session_yml). A human editing the file
     directly, outside Claude Code entirely, always bypasses every rule
     here — that's expected: this hook governs the agent's own tool
     calls, not the developer's own editor.

Note on the marker file: `git checkout <base_branch>` removes
`.bdd/session.yml` from the working tree (it's tracked only on the tmp
branch), so by the time `git merge --squash` actually runs, session.yml is
gone and Rule C can't consult it. `.merge-authorized` is deliberately
never git-added, so checkout never touches it — it survives the branch
switch and is the only thing Rule C's merge/landing-commit checks rely on.

Because session.yml is unreadable on a slice's main branch, an *entirely
unauthorized* merge attempt there (no marker at all) can't be detected by
session.yml either. `sibling_tmp_branch` closes that gap: if the current
branch matches `feature/<slice>-main` and `feature/<slice>-tmp` exists,
Rule C treats it as governed and blocks the merge regardless of whether
any session file is present.

If none of session.yml, the merge marker, or a matching tmp sibling
branch are found, the guard is silent — the plugin never interferes with
work outside a BDD slice.

Exit 0        -> allow
Exit 2 + msg  -> block (stderr message is shown to Claude)
"""
import fnmatch
import json
import os
import re
import subprocess
import sys

def read_stdin_json():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def find_session(start):
    d = os.path.abspath(start)
    while True:
        p = os.path.join(d, ".bdd", "session.yml")
        if os.path.isfile(p):
            return p, d
        parent = os.path.dirname(d)
        if parent == d:
            return None, None
        d = parent


def parse_session(path):
    """Minimal YAML subset parser — avoids a PyYAML dependency."""
    s = {"slice": None, "mode": None, "branch": None, "base_branch": None,
         "current_failing_test": None, "scope_in": [], "scope_out": [],
         "phase": None, "char_status": None, "net_status": None,
         "net_kind": None, "acceptance_status": None,
         "collaborators": [], "cycle": 0, "autonomy": "supervised",
         "failing_test_file": None, "failing_test_name": None,
         "review_scope": None, "review_blocking": None}
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return s
    m = re.search(r"^slice:\s*(\S+)", text, re.M)
    if m: s["slice"] = m.group(1)
    m = re.search(r"^mode:\s*(\S+)", text, re.M)
    if m: s["mode"] = m.group(1)
    m = re.search(r"^phase:\s*(\S+)", text, re.M)
    if m: s["phase"] = m.group(1)
    m = re.search(r"^branch:\s*(\S+)", text, re.M)
    if m: s["branch"] = m.group(1)
    m = re.search(r"^base_branch:\s*(\S+)", text, re.M)
    if m: s["base_branch"] = m.group(1)
    m = re.search(r"^cycle:\s*(\d+)", text, re.M)
    if m: s["cycle"] = int(m.group(1))
    m = re.search(r"^autonomy:\s*(\S+)", text, re.M)
    if m: s["autonomy"] = m.group(1)
    m = re.search(r"^current_failing_test:\s*\n((?:[ \t]+.*\n?)*)", text, re.M)
    if m and re.search(r"\bfile:\s*\S", m.group(1)):
        s["current_failing_test"] = True
        s["failing_test_file"] = re.search(
            r"\bfile:\s*(\S+)", m.group(1)).group(1).strip("'\"")
        nm = re.search(r"\bname:\s*(\S+)", m.group(1))
        if nm: s["failing_test_name"] = nm.group(1).strip("'\"")
    m = re.search(r"^characterisation:\s*\n((?:[ \t]+.*\n?)*)", text, re.M)
    if m:
        st = re.search(r"\bstatus:\s*(\S+)", m.group(1))
        if st: s["char_status"] = st.group(1)
    m = re.search(r"^net:\s*\n((?:[ \t]+.*\n?)*)", text, re.M)
    if m:
        st = re.search(r"\bstatus:\s*(\S+)", m.group(1))
        if st: s["net_status"] = st.group(1)
        k = re.search(r"\bkind:\s*(\S+)", m.group(1))
        if k: s["net_kind"] = k.group(1)
    m = re.search(r"^acceptance_test:\s*\n((?:[ \t]+.*\n?)*)", text, re.M)
    if m:
        st = re.search(r"\bstatus:\s*(\S+)", m.group(1))
        if st: s["acceptance_status"] = st.group(1)
    m = re.search(r"^collaborators:\s*\n((?:[ \t]+.*\n?)*)", text, re.M)
    if m:
        for role_m in re.finditer(
                r"-\s*role:\s*(\S+)\s*\n\s*status:\s*(\S+)", m.group(1)):
            s["collaborators"].append(
                {"role": role_m.group(1), "status": role_m.group(2)})
    m = re.search(r"^review:\s*\n((?:[ \t]+.*\n?)*)", text, re.M)
    if m:
        sc = re.search(r"\bscope:\s*(\S+)", m.group(1))
        if sc: s["review_scope"] = sc.group(1)
        bl = re.search(r"\bblocking:\s*(\d+)", m.group(1))
        if bl: s["review_blocking"] = int(bl.group(1))
    block = re.search(r"^scope:\s*\n((?:[ \t]+.*\n?)*)", text, re.M)
    if block:
        cur = None
        for line in block.group(1).splitlines():
            if re.match(r"\s+in:", line): cur = "scope_in"; continue
            if re.match(r"\s+out:", line): cur = "scope_out"; continue
            if re.match(r"\s+\w+:", line): cur = None; continue
            item = re.match(r"\s+-\s*(\S.*?)\s*(#.*)?$", line)
            if item and cur:
                s[cur].append(item.group(1).strip("'\""))
    return s


PHASE_ORDER = ["characterise", "decompose", "inner", "close", "done"]


def net_status(s):
    """Effective outer-net status for SAFE/FULL_REFACTOR. A `net:` block
    is authoritative; a legacy `characterisation:` block (pre-net schema)
    is honoured as a characterisation-kind net. The net's KIND is never
    checked here — whether existing suites count as the net is the
    human's declaration at scope time, not the guard's judgment."""
    return s["net_status"] or s["char_status"]


NET_FIX = ("declare the existing green suite as the net (/refactor-scope) "
           "or build one (/characterise)")


def extract_new_phase(tool_input):
    """Pull the phase: value an in-flight edit would write, from whichever
    shape the tool uses (Write's content, Edit's new_string, MultiEdit's
    edits[].new_string). Returns None if this edit doesn't touch phase:."""
    chunks = []
    if "content" in tool_input:
        chunks.append(tool_input["content"])
    if "new_string" in tool_input:
        chunks.append(tool_input["new_string"])
    for e in tool_input.get("edits") or []:
        if "new_string" in e:
            chunks.append(e["new_string"])
    text = "\n".join(chunks)
    m = re.search(r"^phase:\s*(\S+)", text, re.M)
    return m.group(1) if m else None


def validate_phase_transition(s, new_phase):
    """Rule D: mechanizes session-schema.md's 'phase transitions are
    forward-only ... commands refuse to run out of order' — without this,
    that line is just prose the agent is trusted to self-enforce, and
    .bdd/session.yml is otherwise unguarded (it must stay writable for the
    commands that legitimately advance it). Returns an error message, or
    None if the transition is legal. Unrecognized phase values (custom
    workflows) are waved through — this only polices the documented ones."""
    old_phase = s["phase"]
    if new_phase not in PHASE_ORDER:
        return None
    if old_phase in PHASE_ORDER and (
            PHASE_ORDER.index(new_phase) < PHASE_ORDER.index(old_phase)):
        return (f"Rule D: phase cannot move backward from '{old_phase}' to "
                f"'{new_phase}'. Phase transitions are forward-only within "
                "a slice (session-schema.md).")

    if new_phase == "decompose" and s["mode"] in ("SAFE", "FULL_REFACTOR"):
        if net_status(s) != "green":
            return ("Rule D: cannot enter phase 'decompose' — the outer "
                    f"net must be green first: {NET_FIX}.")

    if new_phase == "inner":
        if s["mode"] == "GREENFIELD":
            if not s["collaborators"]:
                return ("Rule D: cannot enter phase 'inner' with an empty "
                        "collaborator queue — run /decompose first.")
            if s["acceptance_status"] != "red":
                return ("Rule D: cannot enter phase 'inner' — the "
                        "acceptance test must be recorded red first "
                        "(/scenario).")
        elif s["mode"] in ("SAFE", "FULL_REFACTOR"):
            if net_status(s) != "green":
                return ("Rule D: cannot enter phase 'inner' — the outer "
                        f"net must be green first: {NET_FIX}.")

    if new_phase == "close":
        if not s["collaborators"]:
            return ("Rule D: cannot enter phase 'close' with no "
                    "collaborators recorded — the inner loop queue must be "
                    "worked and drained first (/inner).")
        unfinished = [c["role"] for c in s["collaborators"]
                      if c["status"] != "done"]
        if unfinished:
            return ("Rule D: cannot enter phase 'close' — collaborator(s) "
                     f"still open: {', '.join(unfinished)}. Finish the "
                     "queue with /inner.")

    if new_phase == "done" and old_phase != "close":
        return ("Rule D: cannot enter phase 'done' except from 'close' — "
                "run /review to close the outer loop first.")

    return None


MERGE_MARKER = ".merge-authorized"


def find_merge_marker(start):
    """Untracked marker dropped by /commit-merge --auto. Never git-added,
    so `git checkout <base_branch>` never removes it — unlike
    .bdd/session.yml, which is tracked only on the tmp branch."""
    d = os.path.abspath(start)
    while True:
        p = os.path.join(d, ".bdd", MERGE_MARKER)
        if os.path.isfile(p):
            try:
                text = open(p, encoding="utf-8").read()
            except OSError:
                return None, None
            mb = re.search(r"^branch:\s*(\S+)", text, re.M)
            mm = re.search(r"^base_branch:\s*(\S+)", text, re.M)
            if mb and mm:
                return {"branch": mb.group(1), "base_branch": mm.group(1)}, d
            return None, None
        parent = os.path.dirname(d)
        if parent == d:
            return None, None
        d = parent


def git_branch(cwd):
    try:
        return subprocess.run(
            ["git", "branch", "--show-current"], cwd=cwd,
            capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def sibling_tmp_branch(cwd, br):
    """If br is a slice's feature/<slice>-main and feature/<slice>-tmp
    exists, return the tmp branch name. Lets Rule C recognize a slice's
    main branch as governed even when .bdd/session.yml (tracked only on
    tmp) isn't present in the working tree to prove it."""
    m = re.match(r"^feature/(.+)-main$", br or "")
    if not m:
        return None
    tmp = f"feature/{m.group(1)}-tmp"
    try:
        out = subprocess.run(
            ["git", "branch", "--list", tmp], cwd=cwd,
            capture_output=True, text=True, timeout=5).stdout
        return tmp if out.strip() else None
    except Exception:
        return None


def committed_cycle(cwd):
    """The cycle recorded in HEAD's session.yml — what the working tree's
    cycle is compared against to tell a cycle commit from any other."""
    try:
        text = subprocess.run(
            ["git", "show", "HEAD:.bdd/session.yml"], cwd=cwd,
            capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return 0
    m = re.search(r"^cycle:\s*(\d+)", text, re.M)
    return int(m.group(1)) if m else 0


def pending_changes(cwd):
    """(changed, deleted) paths vs HEAD, staged or not, untracked included
    — what a `git commit -a` (or a prior `git add`) is about to record."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "-uall"], cwd=cwd,
            capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return [], []
    changed, deleted = [], []
    for line in out.splitlines():
        xy, path = line[:2], line[3:]
        if " -> " in path:
            old, path = path.split(" -> ", 1)
            deleted.append(old.strip('"'))
        path = path.strip('"')
        (deleted if "D" in xy else changed).append(path)
    return changed, deleted


TEST_DIR_SEGMENTS = {"tests", "test", "spec", "__tests__"}


def is_test_path(relpath):
    parts = relpath.replace(os.sep, "/").split("/")
    if any(seg in TEST_DIR_SEGMENTS for seg in parts[:-1]):
        return True
    return bool(re.search(
        r"(^test_|_test\.|\.test\.|\.spec\.|Test\.\w+$|_spec\.)", parts[-1]))


def auto_commit_violation(s, root):
    """autonomy: auto — Rule C's commit gate replaces the human who used to
    watch each cycle. Any commit: no test file deleted (shrinking the net
    is a human decision). A cycle commit (cycle advanced past HEAD's):
    the reviewer's verdict for exactly this cycle is recorded with zero
    blocking findings, and — outside SAFE, whose moves are green-to-green
    under the net — a test was added or changed. Returns an error or None."""
    changed, deleted = pending_changes(root)
    gone = [p for p in deleted if is_test_path(p)]
    if gone:
        return (f"deleting test file(s) {', '.join(gone)} — removing tests "
                "is a human decision; log it to /debt instead")
    if s["cycle"] <= committed_cycle(root):
        return None
    want = f"cycle-{s['cycle']}"
    if s["review_scope"] != want or s["review_blocking"] is None:
        return (f"cycle {s['cycle']} has no recorded review — dispatch the "
                f"reviewer and record `review: {{scope: {want}, blocking: "
                "<n>}` in session.yml")
    if s["review_blocking"] > 0:
        return (f"the review of {want} has {s['review_blocking']} open "
                "blocking finding(s) — resolve them, re-review, then commit")
    if s["mode"] != "SAFE" and not any(is_test_path(p) for p in changed):
        return (f"cycle {s['cycle']} commits no test change — every "
                "cycle is driven by its own failing test")
    return None


def bash_targets_session_yml(cmd):
    """True if cmd redirects/tees output at .bdd/session.yml — the way to
    dodge Rule D's phase-transition check, which only inspects Edit/Write/
    MultiEdit tool calls. Deliberately narrow: only unambiguous redirect
    syntax (>, >>, tee) is matched. cp/mv/sed -i/python one-liners are NOT
    caught here — regexing every way Bash can write a file is a losing
    arms race and risks false positives on legitimate reads or the
    documented archive-copy flow. This blocks the accidental case (Claude
    reaching for a shell one-liner instead of the Edit tool), not a
    determined adversarial one."""
    for m in re.finditer(r"(?:>>|>)\s*(\S+)", cmd):
        target = m.group(1).strip("'\"")
        if target.replace(os.sep, "/").endswith(".bdd/session.yml"):
            return True
    m = re.search(r"\btee\b(?:\s+-\S+)*\s+(\S+)", cmd)
    if m:
        target = m.group(1).strip("'\"")
        if target.replace(os.sep, "/").endswith(".bdd/session.yml"):
            return True
    return False


def block(msg):
    sys.stderr.write("[london-bdd guard] " + msg + "\n")
    sys.exit(2)


def rel(path, root):
    try:
        return os.path.relpath(os.path.abspath(path), root)
    except ValueError:
        return path


ALLOWED_DIR_SEGMENTS = {"tests", "test", "adr", "tech-debt", "docs", ".bdd"}
ALLOWED_FILENAMES = {"CLAUDE.md"}


def is_always_allowed(relpath):
    p = relpath.replace(os.sep, "/")
    if p.startswith("."):          # .bdd/, .github/, dotfiles
        return True
    parts = p.split("/")
    if any(seg in ALLOWED_DIR_SEGMENTS for seg in parts[:-1]):
        return True                # tests/ anywhere in the path, nested included
    name = parts[-1]
    return name in ALLOWED_FILENAMES or name.startswith("README")


def unreal_red(s, root):
    """autonomy: auto — with no human watching the red, the recorded
    failing test must at least exist: its file on disk, its name in that
    file. Qualified names (Suite.Name, Class::method) are matched on the
    last segment, which is the part every framework spells out literally.
    Returns an error message, or None if the record points at a real test."""
    f = s["failing_test_file"]
    path = os.path.join(root, f)
    if not os.path.isfile(path):
        return (f"the recorded failing test file '{f}' does not exist")
    name = s["failing_test_name"]
    if name:
        last = re.split(r"\.|::|#|/", name)[-1]
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            text = ""
        if last not in text:
            return (f"the recorded failing test '{name}' is not in '{f}'")
    return None


def is_implementer(payload):
    """True when the tool call comes from the implementer subagent —
    Claude Code puts the subagent's type in the hook payload, namespaced
    by plugin (london-bdd:implementer)."""
    return (payload.get("agent_type") or "").split(":")[-1] == "implementer"


def in_scope(relpath, patterns):
    p = relpath.replace(os.sep, "/")
    for pat in patterns:
        pat = pat.replace(os.sep, "/")
        if fnmatch.fnmatch(p, pat) or p == pat or p.startswith(pat.rstrip("/*") + "/"):
            return True
    return False


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else "edit"
    payload = read_stdin_json()
    tool_input = payload.get("tool_input", {}) or {}
    cwd = payload.get("cwd") or os.getcwd()

    session_path, session_root = find_session(cwd)
    s = parse_session(session_path) if session_path else None
    marker, marker_root = find_merge_marker(cwd)
    root = session_root or marker_root or os.path.abspath(cwd)

    if kind == "bash":
        cmd = tool_input.get("command", "")
        if session_path and bash_targets_session_yml(cmd):
            block("Rule D: writes to .bdd/session.yml via Bash bypass the "
                  "phase-transition check — use the Edit or Write tool so "
                  "the guard can validate the target phase. (Reading or "
                  "archiving the file via Bash is unaffected; this only "
                  "blocks redirect/tee writes at that exact path.)")
        if re.search(r"\bgit\s+(commit|merge|rebase)\b", cmd):
            br = git_branch(root)
            sibling = sibling_tmp_branch(root, br)
            if not session_path and not marker and not sibling:
                sys.exit(0)  # not a slice's tmp, main, or pending merge -> silent
            landing = bool(marker) and br == marker["base_branch"]
            if re.search(r"\bgit\s+commit\b", cmd) and not landing:
                if s and s["branch"] and br != s["branch"]:
                    block(f"Rule C: commits belong on '{s['branch']}' "
                          f"(currently on '{br or 'unknown'}'). Check out the "
                          "slice branch or let the developer commit by hand.")
                if not re.match(r"^feature/.+-tmp$", br):
                    block("Rule C: agent commits are allowed only on a "
                          "feature/<slice>-tmp branch, or the merge-landing "
                          "commit authorized via /commit-merge --auto.")
                if s and s["autonomy"] == "auto":
                    err = auto_commit_violation(s, root)
                    if err:
                        block(f"Rule C (autonomy: auto): commit blocked — "
                              f"{err}.")
            if re.search(r"\bgit\s+rebase\b", cmd):
                block("Rule C: rebases are the developer's manual action, "
                      "never the agent's.")
            if re.search(r"\bgit\s+merge\b", cmd):
                squash_src = re.search(r"--squash\s+(\S+)", cmd)
                authorized = (
                    landing
                    and squash_src is not None
                    and squash_src.group(1) == marker["branch"]
                )
                if not authorized:
                    block("Rule C: merges onto shared branches are the "
                          "developer's manual action, unless authorized via "
                          "`/commit-merge --auto` — a fresh "
                          ".bdd/.merge-authorized marker naming this exact "
                          "tmp branch, while checked out on its "
                          "base_branch, with an exact "
                          "'git merge --squash <tmp>'.")
        sys.exit(0)

    # kind == "edit"
    if not session_path:
        sys.exit(0)  # no active slice -> guard is silent
    fp = tool_input.get("file_path") or tool_input.get("path") or ""
    if not fp:
        sys.exit(0)
    r = rel(fp, root)

    # Rule A — the implementer agent writes production code only. The
    # hook payload names the subagent making the call, so "never edits
    # tests or session.yml" is enforced here, not trusted to its prompt.
    if is_implementer(payload) and is_always_allowed(r):
        block(f"Rule A: the implementer agent may not edit '{r}' — it "
              "writes production code only, never tests, .bdd/ or docs. "
              "If the test looks wrong, stop and report it.")

    # Rule D — phase-transition gate. Checked even though .bdd/ is
    # otherwise always-allowed below: session.yml itself must stay
    # writable for legitimate transitions, but the *value* written must
    # be an earned one.
    if r.replace(os.sep, "/") == ".bdd/session.yml":
        new_phase = extract_new_phase(tool_input)
        if new_phase and new_phase != s["phase"]:
            err = validate_phase_transition(s, new_phase)
            if err:
                block(err)

    if is_always_allowed(r):
        sys.exit(0)  # tests, docs, .bdd, registers are always writable

    # Rule A — red gate. A production edit is licensed by any ONE of:
    #   1. a recorded failing test (the normal inner-loop red)
    #   2. phase == close (wiring the composition root to close the outer loop)
    #   3. SAFE mode with a green net (refactoring moves are green-to-green
    #      by definition; the net is the license — whether it's a trusted
    #      existing suite or a characterisation suite is declared in
    #      session.yml, not decided here)
    licensed = (
        s["current_failing_test"]
        or s["phase"] == "close"
        or (s["mode"] == "SAFE" and net_status(s) == "green")
    )
    if (s["autonomy"] == "auto" and s["current_failing_test"]
            and s["phase"] != "close"):
        err = unreal_red(s, root)
        if err:
            block(f"Rule A (autonomy: auto): production edit to '{r}' is "
                  f"blocked — {err}. Write the test, run it red, then record "
                  "it in session.yml.")
    if not licensed:
        block(f"Rule A: production edit to '{r}' is blocked — no license. "
              "Licenses: a failing test in session.yml (/unit, /acceptance), "
              "phase 'close' (composition-root wiring), or SAFE mode with a "
              f"green net — {NET_FIX}. If this file is not production "
              "code, move it under an allowed path.")

    # Rule B — scope gate
    if s["mode"] in ("SAFE", "FULL_REFACTOR") and s["scope_in"]:
        if in_scope(r, s["scope_out"]):
            block(f"Rule B: '{r}' is declared OUT of scope for this slice. "
                  "Extend the scope explicitly (/refactor-scope) or log the "
                  "temptation to /debt.")
        if not in_scope(r, s["scope_in"]):
            block(f"Rule B ({s['mode']}): '{r}' is outside the declared "
                  "scope. Extend scope explicitly or log to /debt — no "
                  "silent 'while we're here'.")
    sys.exit(0)


if __name__ == "__main__":
    main()
