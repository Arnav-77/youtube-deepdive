"""Reading PASTE_TOKEN for the laptop scripts that call POST /videos.

Imports only os and python-dotenv, never app.db, so it runs with no
DATABASE_URL. That is what lets tests exercise it.

Where the value comes from, in order:
  1. An already-exported PASTE_TOKEN in the shell (load_dotenv never overrides it).
  2. A .env file. With no argument, load_dotenv() searches upward from THIS
     file's folder (evaluation/, then the repository root). So a .env in the
     repository root is found whatever directory the script is started from.
     The server does the same, from app/db.py.
A relative load_dotenv(".env") would instead look in the current directory,
which is why it was not used: started from elsewhere, the script would miss
the file and report the token as missing.
"""

import os

from dotenv import load_dotenv


def load_paste_token(dotenv_path: str | None = None) -> str | None:
    """Return PASTE_TOKEN after loading .env, or None if it is unset or blank.

    Never prints the value. `dotenv_path` is for tests; scripts call it bare.
    """
    load_dotenv(dotenv_path)
    token = os.environ.get("PASTE_TOKEN", "").strip()
    return token or None
