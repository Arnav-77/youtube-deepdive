"""Every script in evaluation/ must import from any working directory.

Running `python evaluation/backfill_titles.py` from the repo root puts
evaluation/ on sys.path, not the repo root, so `import app` fails unless the
script adds the repo root itself with sys.path.insert. This test reproduces
that condition: for each script, a fresh interpreter started from an empty
directory runs the script's import statements and its sys.path lines, in
source order, and nothing else.

Only those statements run. The rest of a script (queries, network calls,
writes to the golden set) is never executed, because some scripts have no
__main__ guard and would act at import time.
"""

import subprocess
import sys
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parent.parent / "evaluation"
SCRIPTS = sorted(p for p in EVAL_DIR.glob("*.py") if p.name != "__init__.py")

# Runs in the fresh interpreter. Takes the script path as its one argument.
PROBE = """
import ast, sys
path = sys.argv[1]
tree = ast.parse(open(path, encoding="utf-8").read(), path)
namespace = {"__file__": path, "__name__": "probe_script"}
for node in tree.body:
    is_import = isinstance(node, (ast.Import, ast.ImportFrom))
    is_path_setup = isinstance(node, ast.Expr) and "sys.path" in ast.unparse(node)
    if is_import or is_path_setup:
        exec(compile(ast.Module([node], []), path, "exec"), namespace)
"""


def test_there_are_scripts_to_check():
    # Guards against the glob silently matching nothing and passing vacuously.
    assert len(SCRIPTS) >= 10


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_imports_when_run_from_another_directory(script, tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", PROBE, str(script)],
        cwd=tmp_path,  # not the repo root, and no .env here
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"{script.name} failed to import from {tmp_path}:\n{result.stderr[-2000:]}"
    )
