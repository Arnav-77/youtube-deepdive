"""Every script in evaluation/ must put the repo root on sys.path before importing `app`.

Running `python evaluation/backfill_titles.py` from the repo root puts
evaluation/ on sys.path, not the repo root, so `from app...` fails unless the
script adds the repo root itself. The fix is one line, `sys.path.insert(...)`,
placed before the first import from `app`.

This test checks that by reading each script's source with `ast`. It runs
nothing: no script is executed, and no separate Python process is started.
A child process would not get conftest.py's network block, and some scripts
act at import time (queries, network calls, writes to the golden set), so
running them from a test is not allowed (see CLAUDE.md, evaluation rules).
"""

import ast
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parent.parent / "evaluation"
SCRIPTS = sorted(p for p in EVAL_DIR.glob("*.py") if p.name != "__init__.py")


def _imports_app(node):
    """True for a top-level `import app...` or `from app... import ...`."""
    if isinstance(node, ast.Import):
        return any(a.name == "app" or a.name.startswith("app.") for a in node.names)
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return node.level == 0 and (module == "app" or module.startswith("app."))
    return False


def _adds_repo_root(node):
    """True for the sys.path line that adds the repo root (the parent of evaluation/)."""
    if not isinstance(node, ast.Expr):
        return False
    text = ast.unparse(node)
    return "sys.path" in text and "parent.parent" in text


def problem(source):
    """None if the source is safe to run from any directory, else what is wrong.

    Walks only the top level of the module, in order. The repo root must be
    added to sys.path before the first top-level import from `app`. A script
    with no top-level import from `app` has nothing to get wrong.
    """
    for node in ast.parse(source).body:
        if _adds_repo_root(node):
            return None
        if _imports_app(node):
            return "imports from app before adding the repo root to sys.path"
    return None


def test_there_are_scripts_to_check():
    # Guards against the glob silently matching nothing and passing vacuously.
    assert len(SCRIPTS) >= 10


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_adds_the_repo_root_before_importing_app(script):
    result = problem(script.read_text(encoding="utf-8"))
    assert result is None, f"{script.name}: {result}"


def test_the_check_flags_an_app_import_with_no_repo_root_setup():
    source = "from pathlib import Path\nfrom app.db import SessionLocal\n"
    assert problem(source) == "imports from app before adding the repo root to sys.path"


def test_the_check_accepts_the_repo_root_setup_before_the_import():
    source = (
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n"
        "from app.db import SessionLocal\n"
    )
    assert problem(source) is None


def test_the_check_ignores_scripts_that_do_not_import_app():
    assert problem("import argparse\nprint('hi')\n") is None
