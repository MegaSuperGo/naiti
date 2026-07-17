"""client.py — HTTP client for the Nexus AI API (same shapes the frontend uses)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import httpx

from .config import ASK_TIMEOUT

MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "csv": "text/csv",
    "txt": "text/plain",
    "pdf": "application/pdf",
}


class ApiError(RuntimeError):
    pass


# The backend catches upstream failures inside the SSE stream and yields them
# as ordinary answer text (chat.py's generate()). Left undetected, a rate-limit
# notice reads as the AI confidently answering — and would be scored as a wrong
# answer, blaming the app for an infrastructure hiccup. Detect and flag them.
RATE_LIMIT_SENTINEL = "rate limit was hit"
BACKEND_ERROR_SENTINELS = [
    RATE_LIMIT_SENTINEL,
    "something went wrong while answering",
    "file generation failed",
]


def classify_backend_text(text: str) -> str:
    low = text.lower()
    if RATE_LIMIT_SENTINEL in low:
        return "rate_limited"
    for s in BACKEND_ERROR_SENTINELS:
        if s in low:
            return "backend_error"
    return ""


@dataclass
class Answer:
    text: str = ""
    sources: list = field(default_factory=list)
    mode: str = ""
    elapsed: float = 0.0
    error: str = ""


class NexusClient:
    def __init__(self, base_url: str, timeout: float = 90.0):
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(timeout=timeout)
        self.token = ""
        self.email = ""
        self.full_name = ""

    def signin(self, company_id: str, email: str, password: str) -> dict:
        r = self.http.post(f"{self.base_url}/api/auth/signin",
                           json={"company_id": company_id, "email": email, "password": password})
        if r.status_code != 200:
            raise ApiError(f"Sign-in failed for {email}: HTTP {r.status_code} {r.text[:160]}")
        d = r.json()
        self.token = d["token"]
        self.email = d["user"]["email"]
        self.full_name = d["user"]["full_name"]
        return d["user"]

    @property
    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def upload(self, filename: str, data: bytes, perm_type: str = "everyone",
               perm_emails: str = "") -> dict:
        ext = filename.rsplit(".", 1)[-1].lower()
        r = self.http.post(
            f"{self.base_url}/api/files/upload",
            headers=self._auth,
            files={"file": (filename, data, MIME.get(ext, "application/octet-stream"))},
            data={"perm_type": perm_type, "perm_emails": perm_emails},
            timeout=120,
        )
        if r.status_code != 200:
            raise ApiError(f"{filename}: HTTP {r.status_code} {r.text[:160]}")
        return r.json()

    def list_files(self) -> list:
        r = self.http.get(f"{self.base_url}/api/files/", headers=self._auth)
        if r.status_code != 200:
            raise ApiError(f"List files failed: HTTP {r.status_code}")
        return r.json()

    def ask(self, question: str) -> Answer:
        """One question, no chat history — every probe is independent."""
        started = time.time()
        ans = Answer()
        try:
            with self.http.stream(
                "POST", f"{self.base_url}/api/chat/",
                headers=self._auth,
                json={"messages": [{"role": "user", "content": question}]},
                timeout=ASK_TIMEOUT,
            ) as r:
                if r.status_code != 200:
                    r.read()
                    ans.error = f"HTTP {r.status_code}: {r.text[:160]}"
                    return ans
                parts = []
                for line in r.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload == "[DONE]":
                        break
                    try:
                        ev = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") == "text":
                        parts.append(ev.get("text", ""))
                    elif ev.get("type") == "sources":
                        ans.sources = ev.get("sources") or []
                    elif ev.get("type") == "mode":
                        ans.mode = ev.get("mode", "")
                ans.text = "".join(parts).strip()
                kind = classify_backend_text(ans.text)
                if kind:
                    ans.error = kind
        except Exception as e:
            ans.error = f"{type(e).__name__}: {e}"
        ans.elapsed = time.time() - started
        return ans

    def close(self) -> None:
        self.http.close()
