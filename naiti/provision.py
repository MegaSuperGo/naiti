"""
provision.py — create, list and remove the test company.

Everything here goes through the app's *own* infrastructure: database.py's
init_company_db and get_global_db, and auth_utils.hash_password. That is
exactly what nexus_ai/backend/admin.py does when an operator creates a company
by hand — naiti just does it non-interactively.

Because the app's modules resolve paths relative to the working directory, the
work runs in a subprocess with cwd set to the backend directory. That keeps
naiti's own process free of the app's import side effects (database.py creates
data/ at import time).
"""
from __future__ import annotations

import json
import subprocess

from .backend import app_python
from .config import Paths

# ── Scripts executed inside the app's own environment ──────────

_CREATE = r'''
import json, sys
sys.path.insert(0, ".")
from database import init_global_db, init_company_db, get_global_db
from auth_utils import hash_password

company_id, company_name, users_json = sys.argv[1], sys.argv[2], sys.argv[3]
users = json.loads(users_json)

init_global_db()
init_company_db(company_id)

conn = get_global_db()
existing = conn.execute("SELECT id FROM companies WHERE id = ?", (company_id,)).fetchone()
conn.execute(
    "INSERT OR REPLACE INTO companies (id, name, active) VALUES (?,?,1)",
    (company_id, company_name),
)
for u in users:
    row = conn.execute(
        "SELECT id FROM users WHERE company_id = ? AND email = ?", (company_id, u["email"])
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE users SET password_hash=?, full_name=?, role=?, whitelisted=1 WHERE id=?",
            (hash_password(u["password"]), u["full_name"], u["role"], row["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO users (company_id, email, password_hash, full_name, role, whitelisted)"
            " VALUES (?,?,?,?,?,1)",
            (company_id, u["email"], hash_password(u["password"]), u["full_name"], u["role"]),
        )
conn.commit()
n = conn.execute("SELECT COUNT(*) FROM users WHERE company_id=?", (company_id,)).fetchone()[0]
conn.close()
print(json.dumps({"existed": bool(existing), "users": n}))
'''

_LIST = r'''
import json, sys, sqlite3
sys.path.insert(0, ".")
from database import get_global_db, company_db_path

conn = get_global_db()
rows = conn.execute(
    "SELECT id, name, created_at, active FROM companies ORDER BY created_at DESC"
).fetchall()
out = []
for r in rows:
    n_users = conn.execute(
        "SELECT COUNT(*) FROM users WHERE company_id=?", (r["id"],)
    ).fetchone()[0]
    docs = 0
    p = company_db_path(r["id"])
    if p.exists():
        try:
            c = sqlite3.connect(str(p))
            docs = c.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            c.close()
        except Exception:
            docs = -1
    out.append({"id": r["id"], "name": r["name"], "created_at": r["created_at"],
                "active": bool(r["active"]), "users": n_users, "documents": docs})
conn.close()
print(json.dumps(out))
'''

_PURGE = r'''
import json, sys
sys.path.insert(0, ".")
from database import get_global_db, company_db_path

company_id = sys.argv[1]
conn = get_global_db()

users = [r["id"] for r in conn.execute(
    "SELECT id FROM users WHERE company_id=?", (company_id,)).fetchall()]
chats = [r["id"] for r in conn.execute(
    "SELECT id FROM chats WHERE company_id=?", (company_id,)).fetchall()]

for uid in users:
    conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
for cid in chats:
    conn.execute("DELETE FROM messages WHERE chat_id=?", (cid,))
conn.execute("DELETE FROM chats WHERE company_id=?", (company_id,))
conn.execute("DELETE FROM users WHERE company_id=?", (company_id,))
conn.execute("DELETE FROM companies WHERE id=?", (company_id,))
conn.commit()
conn.close()

removed_db = False
p = company_db_path(company_id)
for suffix in ("", "-wal", "-shm"):
    f = p.parent / (p.name + suffix)
    if f.exists():
        f.unlink()
        removed_db = True

print(json.dumps({"users": len(users), "chats": len(chats), "db_removed": removed_db}))
'''

_WIPE_DOCS = r'''
import json, sys
sys.path.insert(0, ".")
from database import get_company_db, init_company_db

company_id = sys.argv[1]
init_company_db(company_id)
conn = get_company_db(company_id)
n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
conn.execute("DELETE FROM chunks")
conn.execute("DELETE FROM file_permissions")
conn.execute("DELETE FROM documents")
conn.commit()
conn.close()
print(json.dumps({"deleted": n}))
'''


def _run(paths: Paths, script: str, *args: str) -> dict | list:
    r = subprocess.run(
        [app_python(paths), "-c", script, *args],
        cwd=str(paths.backend),
        capture_output=True,
        text=True,
        timeout=90,
    )
    if r.returncode != 0:
        raise RuntimeError(f"App database call failed:\n{r.stderr.strip()[:600]}")
    out = r.stdout.strip().splitlines()
    return json.loads(out[-1]) if out else {}


# ── Public API ─────────────────────────────────────────────────
def create_company(paths: Paths, company_id: str, company_name: str,
                   users: list[dict]) -> dict:
    return _run(paths, _CREATE, company_id, company_name, json.dumps(users))


def list_companies(paths: Paths) -> list[dict]:
    return _run(paths, _LIST)


def purge_company(paths: Paths, company_id: str) -> dict:
    return _run(paths, _PURGE, company_id)


def wipe_documents(paths: Paths, company_id: str) -> dict:
    """Clear the company's documents without deleting the tenant itself."""
    return _run(paths, _WIPE_DOCS, company_id)
