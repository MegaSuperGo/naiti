"""
backend.py — start and stop the app's own backend for a test run.

naiti runs the real thing: real routers, real retrieval, real database. The
only concession is the port (8123), so a reviewer's own instance on :8000 can
keep running untouched.

The backend is launched with cwd set to the backend directory, which is where
it normally runs from — so database.py's relative DATA_DIR resolves to the
project's real data/ and the test company lives alongside the others as a
normal tenant.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import BACKEND_HOST, BACKEND_PORT, Paths, read_env_key


class BackendError(RuntimeError):
    pass


def port_free(port: int, host: str = BACKEND_HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) != 0


def app_python(paths: Paths) -> str:
    """The interpreter that can import the backend's dependencies.

    Prefer the project's own .venv — that's where fastapi/groq/pdfplumber live.
    naiti itself may be installed in a completely separate environment (pipx,
    Homebrew), so its sys.executable usually cannot run the backend.
    """
    if paths.venv_python.exists():
        return str(paths.venv_python)

    probe = "import fastapi, uvicorn, groq"
    for cand in (sys.executable, "python3"):
        try:
            r = subprocess.run([cand, "-c", probe], capture_output=True, timeout=25)
            if r.returncode == 0:
                return cand
        except Exception:
            continue

    raise BackendError(
        f"No Python found that can run the backend.\n"
        f"  Expected a virtualenv at: {paths.venv_python}\n"
        f"  Create one with:\n"
        f"    cd {paths.app} && python3 -m venv .venv\n"
        f"    .venv/bin/pip install -r nexus_ai/backend/requirements.txt"
    )


def check_backend_deps(paths: Paths) -> list[str]:
    """Which backend imports are missing, if any."""
    py = app_python(paths)
    missing = []
    for mod in ("fastapi", "uvicorn", "groq", "pdfplumber", "docx", "pptx", "openpyxl", "multipart"):
        r = subprocess.run([py, "-c", f"import {mod}"], capture_output=True)
        if r.returncode != 0:
            missing.append(mod)
    return missing


class Backend:
    """A backend process owned by this run."""

    def __init__(self, paths: Paths, port: int = BACKEND_PORT, host: str = BACKEND_HOST):
        self.paths = paths
        self.port = port
        self.host = host
        self.proc: subprocess.Popen | None = None
        self.external = False          # true when we attached to someone else's server
        self.log_path = Path.home() / ".naiti" / "backend.log"
        self._log_fh = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self, timeout: float = 60.0) -> None:
        if not port_free(self.port, self.host):
            raise BackendError(
                f"Port {self.port} is already in use.\n"
                f"  Something else is listening there. Free it, or pass --url to test "
                f"an already-running backend."
            )

        key = read_env_key(self.paths.env_file)
        if not key:
            raise BackendError(
                "No GROQ_API_KEY is set — the AI cannot answer anything without it.\n"
                "  Set it with:  naiti -api <your-groq-key>"
            )

        env = os.environ.copy()
        env["GROQ_API_KEY"] = key
        env["PYTHONUNBUFFERED"] = "1"

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = open(self.log_path, "w")

        self.proc = subprocess.Popen(
            [
                app_python(self.paths), "-m", "uvicorn", "main:app",
                "--host", self.host, "--port", str(self.port),
                "--log-level", "warning",
            ],
            cwd=str(self.paths.backend),   # real data/ — the test company is a normal tenant
            env=env,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise BackendError(f"Backend exited on startup:\n{self.tail()}")
            if self.healthy():
                return
            time.sleep(0.3)
        raise BackendError(f"Backend did not come up within {timeout:.0f}s:\n{self.tail()}")

    def attach(self, url: str) -> None:
        """Use a backend someone else is already running."""
        self.external = True
        parsed = url.rstrip("/")
        self._url = parsed
        try:
            with urllib.request.urlopen(f"{parsed}/api/health", timeout=5) as r:
                if r.status != 200:
                    raise BackendError(f"{parsed} is not healthy (HTTP {r.status})")
        except Exception as e:
            raise BackendError(f"Could not reach a backend at {parsed}: {e}")

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/health", timeout=2) as r:
                return r.status == 200
        except (urllib.error.URLError, ConnectionError, socket.timeout, OSError):
            return False

    def stop(self) -> None:
        if self.external:
            return
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                self.proc.wait(timeout=10)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except Exception:
                    pass
        self.proc = None
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None

    def tail(self, n: int = 20) -> str:
        if not self.log_path.exists():
            return "(no backend log)"
        return "\n".join(self.log_path.read_text().splitlines()[-n:])

    def __enter__(self) -> "Backend":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


class ExternalBackend(Backend):
    """A backend naiti did not start (via --url)."""

    def __init__(self, url: str):
        self._url = url.rstrip("/")
        self.external = True
        self.proc = None
        self._log_fh = None
        self.log_path = Path.home() / ".naiti" / "backend.log"

    @property
    def base_url(self) -> str:
        return self._url

    def start(self, timeout: float = 0) -> None:
        try:
            with urllib.request.urlopen(f"{self._url}/api/health", timeout=5) as r:
                if r.status != 200:
                    raise BackendError(f"{self._url} is not healthy (HTTP {r.status})")
        except BackendError:
            raise
        except Exception as e:
            raise BackendError(f"Could not reach a backend at {self._url}: {e}")

    def stop(self) -> None:
        return
