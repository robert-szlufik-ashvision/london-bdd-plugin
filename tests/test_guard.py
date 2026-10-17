"""Regression suite for scripts/guard.py — the plugin's own hard rules,
tested the way the plugin asks every other project to test: real
behaviour through the real entry point (subprocess, real git repos), not
mocked internals."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parent.parent / "scripts" / "guard.py"


def run_git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                    capture_output=True, text=True)


def init_repo(repo):
    run_git(repo, "init", "-q")
    run_git(repo, "config", "user.email", "t@t.com")
    run_git(repo, "config", "user.name", "t")
    run_git(repo, "commit", "-q", "--allow-empty", "-m", "init")


def write_session(repo, **fields):
    bdd = repo / ".bdd"
    bdd.mkdir(exist_ok=True)
    body = "\n".join(f"{k}: {v}" for k, v in fields.items()) + "\n"
    (bdd / "session.yml").write_text(body)


def write_marker(repo, branch, base_branch):
    bdd = repo / ".bdd"
    bdd.mkdir(exist_ok=True)
    (bdd / ".merge-authorized").write_text(
        f"branch: {branch}\nbase_branch: {base_branch}\n")


def guard(repo, kind, command=None, file_path=None, agent_type=None):
    tool_input = {}
    if command is not None:
        tool_input["command"] = command
    if file_path is not None:
        tool_input["file_path"] = file_path
    body = {"tool_input": tool_input, "cwd": str(repo)}
    if agent_type is not None:
        body["agent_type"] = agent_type
    payload = json.dumps(body)
    proc = subprocess.run(
        [sys.executable, str(GUARD), kind], input=payload,
        capture_output=True, text=True)
    return proc.returncode, proc.stderr


@pytest.fixture
def repo(tmp_path):
    init_repo(tmp_path)
    return tmp_path


def open_slice(repo, slice_name, phase="inner", mode="GREENFIELD", **extra):
    main_br = f"feature/{slice_name}-main"
    tmp_br = f"feature/{slice_name}-tmp"
    run_git(repo, "branch", "-M", main_br)
    run_git(repo, "checkout", "-q", "-b", tmp_br)
    fields = dict(slice=slice_name, mode=mode, phase=phase,
                  branch=tmp_br, base_branch=main_br, **extra)
    write_session(repo, **fields)
    run_git(repo, "add", ".bdd/session.yml")
    run_git(repo, "commit", "-q", "-m", "session")
    return main_br, tmp_br


# ---- Rule A: production edit requires a license ----------------------

def test_blocks_production_edit_with_no_failing_test(repo):
    open_slice(repo, "x")
    (repo / "src").mkdir()
    code, err = guard(repo, "edit", file_path=str(repo / "src" / "order.py"))
    assert code == 2
    assert "Rule A" in err


def test_allows_production_edit_with_recorded_failing_test(repo):
    open_slice(repo, "x", current_failing_test="\n  file: tests/unit/x_test.py\n  name: t")
    code, _ = guard(repo, "edit", file_path=str(repo / "src" / "order.py"))
    assert code == 0


def test_allows_edit_under_tests_dir_regardless_of_license(repo):
    open_slice(repo, "x")
    code, _ = guard(repo, "edit", file_path=str(repo / "tests" / "unit" / "x_test.py"))
    assert code == 0


def test_silent_when_no_active_slice(repo):
    (repo / "src").mkdir()
    code, _ = guard(repo, "edit", file_path=str(repo / "src" / "order.py"))
    assert code == 0


def test_allows_safe_edit_licensed_by_green_existing_net(repo):
    """The net is the license, whatever its kind: a declared existing
    suite licenses green-to-green refactoring without characterisation."""
    open_slice(repo, "x", mode="SAFE",
               **{"net": "\n  kind: existing\n  status: green"})
    code, _ = guard(repo, "edit", file_path=str(repo / "src" / "order.py"))
    assert code == 0


def test_allows_safe_edit_licensed_by_legacy_characterisation_block(repo):
    """Pre-net session files (characterisation: block) keep working."""
    open_slice(repo, "x", mode="SAFE",
               **{"characterisation": "\n  status: green"})
    code, _ = guard(repo, "edit", file_path=str(repo / "src" / "order.py"))
    assert code == 0


def test_blocks_safe_edit_when_declared_net_is_not_green(repo):
    open_slice(repo, "x", mode="SAFE",
               **{"net": "\n  kind: existing\n  status: absent"})
    code, err = guard(repo, "edit", file_path=str(repo / "src" / "order.py"))
    assert code == 2
    assert "Rule A" in err


def test_net_block_overrides_legacy_characterisation_block(repo):
    """When both blocks exist, net: is authoritative — a red net blocks
    even if a stale characterisation block still says green."""
    open_slice(repo, "x", mode="SAFE",
               **{"net": "\n  kind: characterisation\n  status: red",
                  "characterisation": "\n  status: green"})
    code, err = guard(repo, "edit", file_path=str(repo / "src" / "order.py"))
    assert code == 2
    assert "Rule A" in err


# ---- Rule A: the implementer agent never touches tests or .bdd/ -------

FAILING = "\n  file: tests/unit/x_test.py\n  name: t"


def test_blocks_implementer_agent_editing_a_test_file(repo):
    open_slice(repo, "x", current_failing_test=FAILING)
    code, err = guard(repo, "edit", agent_type="london-bdd:implementer",
                      file_path=str(repo / "tests" / "unit" / "x_test.py"))
    assert code == 2
    assert "implementer" in err


def test_blocks_implementer_agent_editing_session_yml(repo):
    open_slice(repo, "x", current_failing_test=FAILING)
    code, err = guard(repo, "edit", agent_type="london-bdd:implementer",
                      file_path=str(repo / ".bdd" / "session.yml"))
    assert code == 2
    assert "implementer" in err


def test_allows_implementer_agent_production_edit_under_red(repo):
    open_slice(repo, "x", current_failing_test=FAILING)
    code, _ = guard(repo, "edit", agent_type="london-bdd:implementer",
                    file_path=str(repo / "src" / "order.py"))
    assert code == 0


# ---- Rule A (autonomy: auto): the recorded red must point at a real test

def write_test(repo, rel, body):
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body)


def test_auto_blocks_production_edit_when_failing_test_file_is_missing(repo):
    open_slice(repo, "x", autonomy="auto", current_failing_test=FAILING)
    code, err = guard(repo, "edit", file_path=str(repo / "src" / "order.py"))
    assert code == 2
    assert "tests/unit/x_test.py" in err


def test_auto_blocks_production_edit_when_test_name_absent_from_file(repo):
    write_test(repo, "tests/unit/x_test.py", "def test_should_other(): ...\n")
    open_slice(repo, "x", autonomy="auto",
               current_failing_test="\n  file: tests/unit/x_test.py"
                                    "\n  name: test_should_place_order")
    code, err = guard(repo, "edit", file_path=str(repo / "src" / "order.py"))
    assert code == 2
    assert "test_should_place_order" in err


def test_auto_allows_production_edit_when_failing_test_exists(repo):
    write_test(repo, "tests/unit/x_test.py",
               "def test_should_place_order(): ...\n")
    open_slice(repo, "x", autonomy="auto",
               current_failing_test="\n  file: tests/unit/x_test.py"
                                    "\n  name: test_should_place_order")
    code, _ = guard(repo, "edit", file_path=str(repo / "src" / "order.py"))
    assert code == 0


def test_auto_matches_qualified_test_name_by_its_last_segment(repo):
    """gtest-style Suite.Name / Class::method records — the file declares
    the parts separately, so only the last segment is looked for."""
    write_test(repo, "tests/unit/repo_test.cpp",
               "TEST(OrderRepository, ShouldSaveAndReturnId) {}\n")
    open_slice(repo, "x", autonomy="auto",
               current_failing_test="\n  file: tests/unit/repo_test.cpp"
                                    "\n  name: OrderRepository.ShouldSaveAndReturnId")
    code, _ = guard(repo, "edit", file_path=str(repo / "src" / "repo.cpp"))
    assert code == 0


# ---- Rule B: scope gate (SAFE/FULL_REFACTOR) --------------------------

def test_blocks_edit_outside_declared_scope(repo):
    open_slice(repo, "x", mode="SAFE")
    # char_status must be green to license the edit at all; set scope too
    bdd = repo / ".bdd" / "session.yml"
    bdd.write_text(bdd.read_text() + (
        "characterisation:\n  status: green\n"
        "scope:\n  in:\n    - src/pricing/*\n  out:\n    - src/tax/*\n"
    ))
    code, err = guard(repo, "edit", file_path=str(repo / "src" / "tax" / "table.py"))
    assert code == 2
    assert "Rule B" in err


def test_allows_edit_inside_declared_scope(repo):
    open_slice(repo, "x", mode="SAFE")
    bdd = repo / ".bdd" / "session.yml"
    bdd.write_text(bdd.read_text() + (
        "characterisation:\n  status: green\n"
        "scope:\n  in:\n    - src/pricing/*\n  out:\n    - src/tax/*\n"
    ))
    code, _ = guard(repo, "edit", file_path=str(repo / "src" / "pricing" / "calc.py"))
    assert code == 0


# ---- Rule C: commits ---------------------------------------------------

def test_blocks_commit_off_the_tmp_branch(repo):
    main_br, tmp_br = open_slice(repo, "x")
    run_git(repo, "checkout", "-q", main_br)
    code, err = guard(repo, "bash", command="git commit -m oops")
    assert code == 2
    assert "Rule C" in err


def test_allows_commit_on_the_tmp_branch(repo):
    open_slice(repo, "x")
    code, _ = guard(repo, "bash", command="git commit -m cycle")
    assert code == 0


def test_rebase_always_blocked_even_with_marker(repo):
    main_br, tmp_br = open_slice(repo, "x", phase="done")
    write_marker(repo, tmp_br, main_br)
    run_git(repo, "checkout", "-q", main_br)
    code, err = guard(repo, "bash", command="git rebase feature/x-tmp")
    assert code == 2
    assert "rebase" in err.lower()


# ---- Rule C (autonomy: auto): a cycle commit carries its own evidence --

def advance_cycle(repo, review="\n  scope: cycle-1\n  blocking: 0", **extra):
    """Rewrite session.yml the way /inner's commit step does: cycle bumped,
    plus the reviewer's recorded verdict for that cycle."""
    fields = dict(slice="x", mode="GREENFIELD", phase="inner",
                  branch="feature/x-tmp", base_branch="feature/x-main",
                  autonomy="auto", cycle=1, **extra)
    if review is not None:
        fields["review"] = review
    write_session(repo, **fields)


def open_auto_slice(repo, mode="GREENFIELD"):
    open_slice(repo, "x", mode=mode, autonomy="auto", cycle=0)


def test_auto_allows_cycle_commit_with_clean_review_and_a_test(repo):
    open_auto_slice(repo)
    write_test(repo, "tests/unit/x_test.py", "def test_should_x(): ...\n")
    advance_cycle(repo)
    code, _ = guard(repo, "bash", command="git commit -am 'cycle 1'")
    assert code == 0


def test_auto_blocks_cycle_commit_without_a_review_for_this_cycle(repo):
    open_auto_slice(repo)
    write_test(repo, "tests/unit/x_test.py", "def test_should_x(): ...\n")
    advance_cycle(repo, review=None)
    code, err = guard(repo, "bash", command="git commit -am 'cycle 1'")
    assert code == 2
    assert "review" in err


def test_auto_blocks_cycle_commit_carrying_a_stale_review(repo):
    open_auto_slice(repo)
    write_test(repo, "tests/unit/x_test.py", "def test_should_x(): ...\n")
    advance_cycle(repo, review="\n  scope: cycle-0\n  blocking: 0")
    code, err = guard(repo, "bash", command="git commit -am 'cycle 1'")
    assert code == 2
    assert "cycle-1" in err


def test_auto_blocks_cycle_commit_with_open_blocking_findings(repo):
    open_auto_slice(repo)
    write_test(repo, "tests/unit/x_test.py", "def test_should_x(): ...\n")
    advance_cycle(repo, review="\n  scope: cycle-1\n  blocking: 2")
    code, err = guard(repo, "bash", command="git commit -am 'cycle 1'")
    assert code == 2
    assert "blocking" in err


def test_auto_blocks_greenfield_cycle_commit_with_no_test_change(repo):
    open_auto_slice(repo)
    (repo / "src").mkdir()
    (repo / "src" / "x.py").write_text("x = 1\n")
    advance_cycle(repo)
    code, err = guard(repo, "bash", command="git commit -am 'cycle 1'")
    assert code == 2
    assert "test" in err


def test_auto_allows_safe_cycle_commit_with_no_test_change(repo):
    """SAFE moves are green-to-green under the net — no new test due."""
    open_auto_slice(repo, mode="SAFE")
    (repo / "src").mkdir()
    (repo / "src" / "x.py").write_text("x = 1\n")
    write_session(repo, slice="x", mode="SAFE", phase="inner",
                  branch="feature/x-tmp", base_branch="feature/x-main",
                  autonomy="auto", cycle=1,
                  review="\n  scope: cycle-1\n  blocking: 0")
    code, _ = guard(repo, "bash", command="git commit -am 'cycle 1'")
    assert code == 0


def test_auto_blocks_commit_that_deletes_a_test_file(repo):
    write_test(repo, "tests/unit/old_test.py", "def test_should_y(): ...\n")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-q", "-m", "old test")
    open_auto_slice(repo)
    (repo / "tests" / "unit" / "old_test.py").unlink()
    code, err = guard(repo, "bash", command="git commit -am 'tidy'")
    assert code == 2
    assert "tests/unit/old_test.py" in err


def test_supervised_cycle_commit_needs_no_recorded_review(repo):
    open_slice(repo, "x", cycle=0)
    write_session(repo, slice="x", mode="GREENFIELD", phase="inner",
                  branch="feature/x-tmp", base_branch="feature/x-main",
                  cycle=1)
    code, _ = guard(repo, "bash", command="git commit -am 'cycle 1'")
    assert code == 0


# ---- Rule C: merges — the auto-merge marker flow ----------------------

def test_blocks_merge_on_slice_main_with_no_marker(repo):
    main_br, tmp_br = open_slice(repo, "x", phase="done")
    run_git(repo, "checkout", "-q", main_br)
    code, err = guard(repo, "bash", command=f"git merge --squash {tmp_br}")
    assert code == 2
    assert "Rule C" in err


def test_allows_merge_with_matching_marker(repo):
    main_br, tmp_br = open_slice(repo, "x", phase="done")
    write_marker(repo, tmp_br, main_br)
    run_git(repo, "checkout", "-q", main_br)
    # checkout removed .bdd/session.yml (tracked only on tmp) — this is
    # the exact gap the marker exists to survive.
    assert not (repo / ".bdd" / "session.yml").exists()
    assert (repo / ".bdd" / ".merge-authorized").exists()
    code, _ = guard(repo, "bash", command=f"git merge --squash {tmp_br}")
    assert code == 0


def test_blocks_merge_when_marker_names_a_different_tmp_branch(repo):
    main_br, tmp_br = open_slice(repo, "x", phase="done")
    write_marker(repo, "feature/other-tmp", main_br)
    run_git(repo, "checkout", "-q", main_br)
    code, err = guard(repo, "bash", command=f"git merge --squash {tmp_br}")
    assert code == 2
    assert "Rule C" in err


def test_allows_landing_commit_after_authorized_merge(repo):
    main_br, tmp_br = open_slice(repo, "x", phase="done")
    write_marker(repo, tmp_br, main_br)
    run_git(repo, "checkout", "-q", main_br)
    run_git(repo, "merge", "--squash", tmp_br)
    code, _ = guard(repo, "bash", command="git commit -m squash")
    assert code == 0


def test_blocks_unauthorized_merge_even_without_any_marker_file(repo):
    """The dangerous case: no session.yml reachable on main (never
    tracked there), no marker at all — must still block, not go silent."""
    main_br, tmp_br = open_slice(repo, "x", phase="done")
    run_git(repo, "checkout", "-q", main_br)
    assert not (repo / ".bdd").exists()
    code, err = guard(repo, "bash", command=f"git merge --squash {tmp_br}")
    assert code == 2
    assert "Rule C" in err


def test_silent_for_unrelated_repo_with_no_slice_at_all(repo):
    run_git(repo, "checkout", "-q", "-b", "main-line")
    code, _ = guard(repo, "bash", command="git merge --squash some-other-branch")
    assert code == 0


# ---- Rule D: phase transitions can't skip a step ----------------------

def edit_session(repo, new_phase):
    """Simulate an Edit tool call that rewrites the phase: line in
    .bdd/session.yml, the way a command would. Fixture state (collaborators,
    acceptance_test, characterisation) is seeded via open_slice's **extra
    before this is called."""
    bdd = repo / ".bdd" / "session.yml"
    payload = json.dumps({
        "tool_input": {
            "file_path": str(bdd),
            "old_string": "phase: " + _current_phase(bdd),
            "new_string": f"phase: {new_phase}",
        },
        "cwd": str(repo),
    })
    proc = subprocess.run(
        [sys.executable, str(GUARD), "edit"], input=payload,
        capture_output=True, text=True)
    return proc.returncode, proc.stderr


def _current_phase(session_file):
    for line in session_file.read_text().splitlines():
        if line.startswith("phase:"):
            return line.split(":", 1)[1].strip()
    return ""


def test_blocks_phase_moving_backward(repo):
    open_slice(repo, "x", phase="inner",
               **{"collaborators": "\n  - role: A\n    status: done"})
    code, err = edit_session(repo, "decompose")
    assert code == 2
    assert "Rule D" in err


def test_blocks_close_with_unfinished_collaborator(repo):
    open_slice(repo, "x", phase="inner",
               **{"collaborators": "\n  - role: A\n    status: red"})
    code, err = edit_session(repo, "close")
    assert code == 2
    assert "Rule D" in err
    assert "A" in err


def test_blocks_close_with_no_collaborators(repo):
    open_slice(repo, "x", phase="inner")
    code, err = edit_session(repo, "close")
    assert code == 2
    assert "Rule D" in err


def test_allows_close_when_all_collaborators_done(repo):
    open_slice(repo, "x", phase="inner",
               **{"collaborators": "\n  - role: A\n    status: done"})
    code, _ = edit_session(repo, "close")
    assert code == 0


def test_blocks_inner_greenfield_with_empty_queue(repo):
    open_slice(repo, "x", phase="decompose", mode="GREENFIELD")
    code, err = edit_session(repo, "inner")
    assert code == 2
    assert "Rule D" in err


def test_blocks_inner_greenfield_without_red_acceptance_test(repo):
    open_slice(repo, "x", phase="decompose", mode="GREENFIELD",
               **{"collaborators": "\n  - role: A\n    status: pending"})
    code, err = edit_session(repo, "inner")
    assert code == 2
    assert "Rule D" in err


def test_allows_inner_greenfield_with_queue_and_red_at(repo):
    open_slice(repo, "x", phase="decompose", mode="GREENFIELD",
               **{"collaborators": "\n  - role: A\n    status: pending",
                  "acceptance_test": "\n  status: red"})
    code, _ = edit_session(repo, "inner")
    assert code == 0


def test_blocks_inner_safe_without_green_characterisation(repo):
    open_slice(repo, "x", phase="characterise", mode="SAFE")
    code, err = edit_session(repo, "inner")
    assert code == 2
    assert "Rule D" in err


def test_allows_inner_safe_with_green_characterisation(repo):
    open_slice(repo, "x", phase="characterise", mode="SAFE",
               **{"characterisation": "\n  status: green"})
    code, _ = edit_session(repo, "inner")
    assert code == 0


def test_allows_inner_safe_with_green_existing_net(repo):
    """/refactor-scope declared the existing suites the net — /characterise
    is skipped and the slice may enter inner directly."""
    open_slice(repo, "x", phase="characterise", mode="SAFE",
               **{"net": "\n  kind: existing\n  status: green"})
    code, _ = edit_session(repo, "inner")
    assert code == 0


def test_blocks_inner_safe_when_existing_net_not_verified_green(repo):
    open_slice(repo, "x", phase="characterise", mode="SAFE",
               **{"net": "\n  kind: existing\n  status: absent"})
    code, err = edit_session(repo, "inner")
    assert code == 2
    assert "Rule D" in err


def test_allows_decompose_full_refactor_with_green_existing_net(repo):
    open_slice(repo, "x", phase="characterise", mode="FULL_REFACTOR",
               **{"net": "\n  kind: existing\n  status: green"})
    code, _ = edit_session(repo, "decompose")
    assert code == 0


def test_blocks_done_not_from_close(repo):
    open_slice(repo, "x", phase="inner",
               **{"collaborators": "\n  - role: A\n    status: done"})
    code, err = edit_session(repo, "done")
    assert code == 2
    assert "Rule D" in err


def test_allows_done_from_close(repo):
    open_slice(repo, "x", phase="close")
    code, _ = edit_session(repo, "done")
    assert code == 0


# ---- Rule D (autonomy: auto): the human's declarations are fixed -------

def rewrite_session(repo, old, new):
    bdd = repo / ".bdd" / "session.yml"
    body = {"tool_input": {"file_path": str(bdd), "old_string": old,
                           "new_string": new}, "cwd": str(repo)}
    proc = subprocess.run(
        [sys.executable, str(GUARD), "edit"], input=json.dumps(body),
        capture_output=True, text=True)
    return proc.returncode, proc.stderr


SCOPED = {"net": "\n  kind: existing\n  status: green",
          "scope": "\n  in:\n    - src/pricing/*\n  out:\n    - src/tax/*"}


def test_auto_blocks_agent_widening_the_declared_scope(repo):
    open_slice(repo, "x", mode="SAFE", autonomy="auto", **SCOPED)
    code, err = rewrite_session(repo, "    - src/pricing/*",
                                "    - src/pricing/*\n    - src/tax/*")
    assert code == 2
    assert "scope" in err


def test_auto_blocks_agent_changing_the_mode(repo):
    open_slice(repo, "x", mode="SAFE", autonomy="auto", **SCOPED)
    code, err = rewrite_session(repo, "mode: SAFE", "mode: FULL_REFACTOR")
    assert code == 2
    assert "mode" in err


def test_auto_blocks_agent_redeclaring_the_net_kind(repo):
    open_slice(repo, "x", mode="SAFE", autonomy="auto",
               **{"net": "\n  kind: characterisation\n  status: green"})
    code, err = rewrite_session(repo, "kind: characterisation",
                                "kind: existing")
    assert code == 2
    assert "net" in err


def test_blocks_agent_switching_a_supervised_slice_to_auto(repo):
    open_slice(repo, "x", autonomy="supervised")
    code, err = rewrite_session(repo, "autonomy: supervised",
                                "autonomy: auto")
    assert code == 2
    assert "autonomy" in err


def test_allows_agent_handing_an_auto_slice_back_to_supervised(repo):
    open_slice(repo, "x", autonomy="auto")
    code, _ = rewrite_session(repo, "autonomy: auto",
                              "autonomy: supervised")
    assert code == 0


def test_auto_allows_ordinary_bookkeeping_edits(repo):
    open_slice(repo, "x", mode="SAFE", autonomy="auto", cycle=0, **SCOPED)
    code, _ = rewrite_session(repo, "cycle: 0", "cycle: 1")
    assert code == 0


def test_auto_blocks_done_without_a_clean_slice_review(repo):
    open_slice(repo, "x", phase="close", autonomy="auto",
               review="\n  scope: cycle-3\n  blocking: 0")
    code, err = edit_session(repo, "done")
    assert code == 2
    assert "slice" in err


def test_auto_allows_done_with_a_clean_slice_review(repo):
    open_slice(repo, "x", phase="close", autonomy="auto")
    code, _ = rewrite_session(
        repo, "phase: close",
        "phase: done\nreview:\n  scope: slice\n  blocking: 0")
    assert code == 0


# ---- Rule D: Bash can't dodge the phase-transition check --------------

def test_blocks_bash_redirect_write_to_session_yml(repo):
    open_slice(repo, "x", phase="inner")
    code, err = guard(repo, "bash",
                       command="echo 'phase: close' > .bdd/session.yml")
    assert code == 2
    assert "Rule D" in err


def test_blocks_bash_tee_write_to_session_yml(repo):
    open_slice(repo, "x", phase="inner")
    code, err = guard(repo, "bash",
                       command="echo 'phase: close' | tee .bdd/session.yml")
    assert code == 2
    assert "Rule D" in err


def test_allows_bash_read_of_session_yml(repo):
    open_slice(repo, "x", phase="inner")
    code, _ = guard(repo, "bash", command="cat .bdd/session.yml")
    assert code == 0


def test_allows_bash_redirect_that_does_not_target_session_yml(repo):
    open_slice(repo, "x", phase="inner")
    code, _ = guard(repo, "bash",
                     command="cat .bdd/session.yml > /tmp/session-copy.yml")
    assert code == 0


def test_silent_bash_write_to_session_yml_when_no_active_slice(repo):
    code, _ = guard(repo, "bash",
                     command="echo 'phase: inner' > .bdd/session.yml")
    assert code == 0
