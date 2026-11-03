# Design decisions

## 3 Nov 2026: tests isolate configuration in conftest.py, not by changing app code

**Decision.** The test suite runs with no network, no database and no .env. tests/conftest.py, before any app module is imported, disables python-dotenv (PYTHON_DOTENV_DISABLED), overwrites DATABASE_URL with an unreachable local address, tells LiteLLM to use its bundled price list (LITELLM_LOCAL_MODEL_COST_MAP), removes GEMINI_API_KEY, and replaces Python's socket connect so any HTTP call (Gemini, GitHub, YouTube) raises. The socket guard does not stop the database driver: psycopg connects through libpq in C, bypassing Python's socket module (verified 3 Nov). The overwritten DATABASE_URL is the database protection.

**Why it was needed.** Every module under test imports app/db.py, directly or through app.models or app.embeddings. db.py reads .env and raises at import if DATABASE_URL is unset. Importing litellm downloads a price list from GitHub.

**Alternatives considered.**
- Make db.py create the engine lazily, inside a function. Rejected: changes production code in a test branch, and design rule 7 wants missing configuration to stop the app at startup, which db.py does correctly now.
- Move the pure functions into modules that never import the database. Rejected for now for the same reason; worth revisiting if test import time becomes a problem.

**Also decided.** pytest goes in requirements-dev.txt, not requirements.txt. The Dockerfile installs only requirements.txt, so test tools never reach Render. tests/, pytest.ini and requirements-dev.txt are in .dockerignore so test-only commits do not change the image.
