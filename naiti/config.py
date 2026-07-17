"""
config.py — locating the app under test, and run-wide constants.

naiti is a separate tool from the app it tests. It finds nexus_what on disk,
imports nothing from it at module level, and drives it purely over HTTP plus
the app's own database helpers.
"""
from __future__ import annotations

import os
from pathlib import Path

# ── Ports ──────────────────────────────────────────────────────
# 8000 is the app's own backend and 8080 its frontend. naiti stays off both so
# a reviewer's running dev instance is never disturbed by a test run.
BACKEND_PORT = 8123
BACKEND_HOST = "127.0.0.1"

# ── Run tuning ─────────────────────────────────────────────────
UPLOAD_WORKERS = 4
ASK_DELAY = 1.0            # pause between questions; Groq's free tier is 8k TPM
ASK_TIMEOUT = 180
JUDGE_MODEL = "openai/gpt-oss-20b"

DEFAULT_COMPANY_ID = "INTERNAL-TESTING-5312"
DEFAULT_SEED = 20260717

# Every naiti account uses this password. The company is a disposable test
# tenant; there is nothing real behind these logins.
TEST_PASSWORD = "NaitiTest!2026"

APP_MARKER = Path("nexus_ai") / "backend" / "main.py"


class AppNotFound(RuntimeError):
    pass


def find_app(explicit: str | None = None) -> Path:
    """Locate the nexus_what checkout.

    Order: --app flag, then $NAITI_APP, then the working directory and its
    parents, then a couple of conventional spots. Raises with a useful message
    rather than guessing wrong.
    """
    candidates: list[Path] = []

    if explicit:
        candidates.append(Path(explicit).expanduser().resolve())
    if os.environ.get("NAITI_APP"):
        candidates.append(Path(os.environ["NAITI_APP"]).expanduser().resolve())

    here = Path.cwd().resolve()
    for d in [here, *here.parents]:
        candidates.append(d)
        candidates.append(d / "nexus_what")

    # Deliberately no fallback to a hardcoded home directory: naiti is
    # installed globally on other people's machines, and silently testing
    # whatever checkout happens to sit in ~/Documents is worse than saying
    # plainly that it can't find one.

    seen = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        if (c / APP_MARKER).exists():
            return c
        # Tolerate being pointed at the backend dir itself.
        if c.name == "backend" and (c / "main.py").exists():
            return c.parent.parent

    raise AppNotFound(
        "Could not find the nexus_what project.\n"
        "  Point naiti at it with:  naiti --app /path/to/nexus_what\n"
        "  or set:                  export NAITI_APP=/path/to/nexus_what\n"
        "  (looking for nexus_ai/backend/main.py inside it)"
    )


class Paths:
    """Everything naiti needs from an app checkout."""

    def __init__(self, app_root: Path):
        self.app = app_root
        self.backend = app_root / "nexus_ai" / "backend"
        self.env_file = self.backend / ".env"
        self.data = self.backend / "data"
        self.venv_python = app_root / ".venv" / "bin" / "python"
        if os.name == "nt":
            self.venv_python = app_root / ".venv" / "Scripts" / "python.exe"

    def company_db(self, company_id: str) -> Path:
        return self.data / f"{company_id}.db"

    def __repr__(self) -> str:
        return f"<Paths app={self.app}>"


def read_env_key(env_file: Path, name: str = "GROQ_API_KEY") -> str:
    if not env_file.exists():
        return ""
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith(name):
            _, _, val = line.partition("=")
            return val.strip().strip('"').strip("'")
    return ""


def write_env_key(env_file: Path, value: str, name: str = "GROQ_API_KEY") -> str:
    """Set (or replace) a key in the app's .env, preserving every other line.

    Returns 'created' | 'updated' | 'unchanged'.
    """
    env_file.parent.mkdir(parents=True, exist_ok=True)

    if not env_file.exists():
        env_file.write_text(f"{name}={value}\n")
        return "created"

    lines = env_file.read_text().splitlines()
    out, found, changed = [], False, False
    for line in lines:
        if line.strip().startswith(name) and "=" in line:
            found = True
            existing = line.partition("=")[2].strip().strip('"').strip("'")
            if existing == value:
                out.append(line)
            else:
                out.append(f"{name}={value}")
                changed = True
        else:
            out.append(line)

    if not found:
        out.append(f"{name}={value}")
        changed = True

    env_file.write_text("\n".join(out) + "\n")
    return "updated" if changed else "unchanged"
