"""Tests for evaluation/paste_token.py: where the laptop scripts read PASTE_TOKEN.

Each case runs in a child process with a clean environment (no PASTE_TOKEN,
no PYTHON_DOTENV_DISABLED from conftest) and a working directory that is not
the repository, so the answer does not depend on what the test runner has
loaded. The token values are dummies written into temporary files. The
child prints only FOUND or MISSING and the token's length, never the value.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DUMMY = "dummy-token-for-tests-only"


def run_helper(tmp_path, dotenv_text=None, shell_value=None):
    dotenv = tmp_path / "test.env"
    if dotenv_text is not None:
        dotenv.write_text(dotenv_text, encoding="utf-8")

    env = {k: v for k, v in os.environ.items()
           if k not in ("PASTE_TOKEN", "PYTHON_DOTENV_DISABLED")}
    if shell_value is not None:
        env["PASTE_TOKEN"] = shell_value

    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO / 'evaluation')!r})\n"
        "from paste_token import load_paste_token\n"
        f"token = load_paste_token({str(dotenv)!r})\n"
        "print('FOUND' if token else 'MISSING', len(token) if token else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    status, length = result.stdout.split()
    return status, int(length)


def test_a_token_only_in_dotenv_is_seen_when_nothing_is_exported(tmp_path):
    # The case that matters: .env holds the token, the shell does not.
    assert run_helper(tmp_path, dotenv_text=f"PASTE_TOKEN={DUMMY}\n") == ("FOUND", len(DUMMY))


def test_the_working_directory_does_not_matter(tmp_path):
    # Run from tmp_path, a directory with no .env in it. The file is passed
    # explicitly, so only the loading rule is being tested.
    other = tmp_path / "elsewhere"
    other.mkdir()
    dotenv = tmp_path / "test.env"
    dotenv.write_text(f"PASTE_TOKEN={DUMMY}\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("PASTE_TOKEN", "PYTHON_DOTENV_DISABLED")}
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO / 'evaluation')!r})\n"
        "from paste_token import load_paste_token\n"
        f"print(len(load_paste_token({str(dotenv)!r}) or ''))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=other,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert int(out.stdout.strip()) == len(DUMMY)


def test_a_token_exported_in_the_shell_is_used_without_any_file(tmp_path):
    assert run_helper(tmp_path, shell_value=DUMMY) == ("FOUND", len(DUMMY))


def test_an_exported_token_takes_precedence_over_dotenv(tmp_path):
    # load_dotenv never overrides a variable already set, so the shell wins.
    shell = "shell-value-that-is-longer-than-the-file-value"
    assert run_helper(tmp_path, dotenv_text="PASTE_TOKEN=short\n", shell_value=shell) \
        == ("FOUND", len(shell))


def test_neither_source_gives_missing(tmp_path):
    assert run_helper(tmp_path) == ("MISSING", 0)


def test_a_blank_token_in_dotenv_counts_as_missing(tmp_path):
    assert run_helper(tmp_path, dotenv_text="PASTE_TOKEN=\n") == ("MISSING", 0)


def test_a_blank_exported_token_counts_as_missing(tmp_path):
    assert run_helper(tmp_path, shell_value="   ") == ("MISSING", 0)


def test_the_helper_does_not_import_the_database_layer():
    # It must run with no DATABASE_URL, so no import in it may reach app.*.
    import ast

    tree = ast.parse((REPO / "evaluation" / "paste_token.py").read_text(encoding="utf-8"))
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    assert not [m for m in modules if m == "app" or m.startswith("app.")]
