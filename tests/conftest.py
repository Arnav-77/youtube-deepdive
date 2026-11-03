"""Make the test run offline and unable to see real configuration.

Everything here runs at import time, at module top level, on purpose.
pytest imports this file before any test file, and the test files import
app modules at their top. A fixture would run too late: by then app.db
would already have read .env and checked DATABASE_URL.

Why each line is needed:
  - app/db.py calls load_dotenv() and raises if DATABASE_URL is unset. Every
    module under test imports it (chunking and youtube via app.models,
    generation and run_eval via app.embeddings).
  - Importing litellm downloads its model price list from GitHub unless
    told to use the bundled copy.
"""

import os
import socket

# 1. load_dotenv() becomes a no-op, so .env is never opened.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

# 2. Overwritten, not setdefault: if the shell exports the real URL, the tests
#    still never see it. Port 9 on localhost, and the socket guard below means
#    nothing could connect to it anyway. create_engine does not connect until a
#    query runs, so this only satisfies db.py's startup check.
os.environ["DATABASE_URL"] = "postgresql+psycopg://nobody@127.0.0.1:9/never"

# 3. LiteLLM uses its bundled price list instead of fetching one.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

# 4. A mistaken call into the embedding code fails before sending anything.
os.environ.pop("GEMINI_API_KEY", None)


# 5. The proof behind "no network": any attempt to open a connection, from an
#    import or a test, fails loudly instead of quietly working.
def _no_network(*args, **kwargs):
    raise RuntimeError("network disabled in tests")


socket.socket.connect = _no_network
socket.socket.connect_ex = _no_network
socket.create_connection = _no_network
