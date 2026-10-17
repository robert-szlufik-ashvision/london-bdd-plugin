"""Regression suite for scripts/next.py — the deterministic 'what's next'
lookup, tested through the real entry point against real temp git repos,
matching test_guard.py's conventions."""
import subprocess
import sys
from pathlib import Path

from test_guard import init_repo, open_slice  # noqa: F401 (fixture reuse)

NEXT = Path(__file__).resolve().parent.parent / "scripts" / "next.py"


def run_next(repo):
    proc = subprocess.run(
        [sys.executable, str(NEXT), str(repo)],
        capture_output=True, text=True)
    return proc.stdout.strip()


def test_no_session_suggests_slice_entry_points(tmp_path):
    init_repo(tmp_path)
    out = run_next(tmp_path)
    assert "/scenario" in out
    assert "/refactor-scope" in out


def test_phase_characterise_suggests_characterise(tmp_path):
    init_repo(tmp_path)
    open_slice(tmp_path, "x", phase="characterise", mode="SAFE")
    out = run_next(tmp_path)
    assert out.startswith("next: /characterise")


def test_phase_decompose_suggests_decompose(tmp_path):
    init_repo(tmp_path)
    open_slice(tmp_path, "x", phase="decompose", mode="GREENFIELD")
    out = run_next(tmp_path)
    assert out.startswith("next: /decompose")


def test_phase_inner_with_pending_collaborator_names_it(tmp_path):
    init_repo(tmp_path)
    open_slice(tmp_path, "x", phase="inner",
               **{"collaborators": "\n  - role: OrderRepository\n    status: pending"})
    out = run_next(tmp_path)
    assert out.startswith("next: /inner")
    assert "OrderRepository" in out


def test_phase_inner_with_recorded_failing_test(tmp_path):
    init_repo(tmp_path)
    open_slice(
        tmp_path, "x", phase="inner",
        **{"collaborators": "\n  - role: OrderRepository\n    status: pending",
           "current_failing_test": "\n  file: tests/unit/x_test.py\n  name: t"})
    out = run_next(tmp_path)
    assert out.startswith("next: /inner")
    assert "failing test" in out


def test_phase_inner_with_all_collaborators_done_flags_review(tmp_path):
    init_repo(tmp_path)
    open_slice(tmp_path, "x", phase="inner",
               **{"collaborators": "\n  - role: A\n    status: done"})
    out = run_next(tmp_path)
    assert out.startswith("next: /review")


def test_phase_close_suggests_review(tmp_path):
    init_repo(tmp_path)
    open_slice(tmp_path, "x", phase="close")
    out = run_next(tmp_path)
    assert out.startswith("next: /review")


def test_phase_done_suggests_commit_merge(tmp_path):
    init_repo(tmp_path)
    open_slice(tmp_path, "x", phase="done")
    out = run_next(tmp_path)
    assert out.startswith("next: /commit-merge")


def test_auto_slice_says_to_continue_unattended(tmp_path):
    init_repo(tmp_path)
    open_slice(tmp_path, "x", phase="decompose", autonomy="auto")
    out = run_next(tmp_path)
    assert out.startswith("next: /decompose")
    assert "unattended" in out


def test_supervised_slice_does_not_claim_to_run_unattended(tmp_path):
    init_repo(tmp_path)
    open_slice(tmp_path, "x", phase="decompose")
    assert "unattended" not in run_next(tmp_path)


def test_handed_back_slice_names_the_developer_and_the_reason(tmp_path):
    init_repo(tmp_path)
    open_slice(tmp_path, "x", phase="close", autonomy="supervised",
               handback='"net red on verification run"')
    out = run_next(tmp_path)
    assert out.startswith("next: developer")
    assert "net red on verification run" in out
