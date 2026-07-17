"""
runner.py — one test run, start to finish.

  provision company (via the app's own DB layer) → start backend on :8123 →
  sign in six staff → upload 500 real Office/CSV/text files → ask questions as
  an ordinary member → grade → tally.

The runner is UI-agnostic: it pushes events to a callback, and cli.py renders
them. Forecast results are tallied separately from factual ones so a
judgement-call category can never quietly move the factual score.
"""
from __future__ import annotations

import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable

from . import corpus, provision, quota
from .backend import Backend, ExternalBackend
from .client import NexusClient
from .config import (
    ASK_DELAY,
    TEST_PASSWORD,
    UPLOAD_WORKERS,
    Paths,
    read_env_key,
)
from .grader import Verdict, judge_verdict, rule_verdict

Emit = Callable[[dict], None]

KIND_LABELS = {
    "financial_lookup": "Financial lookup",
    "computation": "Computation",
    "trend": "Trend analysis",
    "aggregation": "Aggregation",
    "permission": "Permission leakage",
    "refusal": "Refusal / hallucination",
    "forecast": "Forecasting (grounding)",
}

# Forecasting is a judgement call, not a fact lookup. It is scored and shown,
# but kept out of the factual headline so neither side of the number is
# distorted by it.
JUDGEMENT_KINDS = {"forecast"}

# Waits between rate-limit retries, in seconds.
RATE_LIMIT_BACKOFF = (20, 45, 75)


@dataclass
class Result:
    qid: str
    kind: str
    prompt: str
    expected: str
    answer: str
    sources: list = field(default_factory=list)
    mode: str = ""
    elapsed: float = 0.0
    rule_ok: bool = False
    rule_reason: str = ""
    judge_ok: bool = False
    judge_reason: str = ""
    judged: bool = False
    error: str = ""

    @property
    def agree(self) -> bool:
        return (not self.judged) or (self.rule_ok == self.judge_ok)


def spread(questions: list, limit: int | None) -> list:
    """Trim to `limit` while keeping every category represented.

    Plain truncation would make a short run test only the first category and
    silently skip the security probes, which are the ones that matter most.
    """
    if not limit or limit >= len(questions):
        return questions
    buckets = defaultdict(list)
    for q in questions:
        buckets[q.kind].append(q)
    picked, i = [], 0
    while len(picked) < limit:
        added = False
        for kind in KIND_LABELS:
            if i < len(buckets.get(kind, [])):
                picked.append(buckets[kind][i])
                added = True
                if len(picked) == limit:
                    break
        if not added:
            break
        i += 1
    order = {id(q): n for n, q in enumerate(questions)}
    return sorted(picked, key=lambda q: order[id(q)])


class Runner:
    def __init__(self, paths: Paths, company_id: str, emit: Emit, *,
                 seed: int, use_judge: bool, question_limit: int | None = None,
                 url: str | None = None, keep: bool = False, fresh: bool = True,
                 upload_only: bool = False, only: str | None = None,
                 delay: float | None = None):
        self.paths = paths
        self.company_id = company_id
        self.emit = emit
        self.seed = seed
        self.use_judge = use_judge
        self.question_limit = question_limit
        self.url = url
        self.keep = keep
        self.fresh = fresh
        self.upload_only = upload_only
        self.only = only
        self.delay = ASK_DELAY if delay is None else delay
        self.cancelled = False
        self.results: list[Result] = []

    def log(self, msg: str, level: str = "info") -> None:
        self.emit({"type": "log", "level": level, "msg": msg})

    def cancel(self) -> None:
        self.cancelled = True

    # ── Main ───────────────────────────────────────────────────
    def run(self) -> dict:
        started = time.time()
        company = corpus.generate(self.company_id, seed=self.seed)
        questions = company.questions
        if self.only:
            questions = [q for q in questions if q.kind == self.only]
        questions = spread(questions, self.question_limit)

        self.emit({"type": "company", "name": company.name, "company_id": self.company_id,
                   "stats": company.stats, "questions": len(questions)})

        backend = ExternalBackend(self.url) if self.url else Backend(self.paths)
        try:
            # 1. Provision through the app's own infrastructure
            self.emit({"type": "phase", "phase": "provision", "label": "Provisioning company"})
            info = provision.create_company(
                self.paths, self.company_id, company.name,
                [{"email": w.email, "full_name": w.full_name, "role": w.role,
                  "password": TEST_PASSWORD} for w in company.workers],
            )
            self.log(f"Company {self.company_id} "
                     f"{'reused' if info['existed'] else 'created'} with {info['users']} users", "ok")

            if self.fresh:
                wiped = provision.wipe_documents(self.paths, self.company_id)
                if wiped["deleted"]:
                    self.log(f"Cleared {wiped['deleted']} documents from the previous run")

            # 2. Backend
            self.emit({"type": "phase", "phase": "backend", "label": "Starting backend"})
            if self.url:
                self.log(f"Using the backend already running at {self.url}")
            else:
                self.log(f"Launching the app's backend on {backend.base_url}")
            backend.start()
            self.log("Backend healthy", "ok")

            # 3. Sign in
            clients = []
            for w in company.workers:
                c = NexusClient(backend.base_url)
                c.signin(self.company_id, w.email, TEST_PASSWORD)
                clients.append(c)
            self.log(f"Signed in {len(clients)} staff accounts", "ok")

            # 4. Upload
            self.emit({"type": "phase", "phase": "upload", "label": "Uploading documents",
                       "total": len(company.docs)})
            self._upload(company, clients)
            if self.cancelled:
                return self._finish(company, started, backend, cancelled=True)

            if self.upload_only:
                self.emit({"type": "seeded", "company_id": self.company_id,
                           "password": TEST_PASSWORD,
                           "users": [w.email for w in company.workers],
                           "files": len(company.docs)})
                return {}

            # 5. Ask
            self.emit({"type": "phase", "phase": "ask", "label": "Asking questions",
                       "total": len(questions)})
            self._ask(questions, clients[company.stats["asker_idx"]])

            return self._finish(company, started, backend)

        except Exception as e:
            self.log(f"{type(e).__name__}: {e}", "error")
            self.emit({"type": "failed", "error": str(e)})
            return {}
        finally:
            backend.stop()
            if not self.keep and not self.upload_only:
                try:
                    provision.purge_company(self.paths, self.company_id)
                    self.log(f"Removed test company {self.company_id} "
                             f"(use --keep to leave it in place)")
                except Exception as e:
                    self.log(f"Could not remove {self.company_id}: {e}", "warn")

    # ── Phases ─────────────────────────────────────────────────
    def _upload(self, company, clients) -> None:
        done = failed = 0
        errors: list[str] = []
        t0 = time.time()

        def one(d):
            try:
                clients[d.uploader % len(clients)].upload(
                    d.filename, d.data, d.perm_type, d.perm_emails)
                return None
            except Exception as e:
                return f"{d.filename}: {e}"

        with ThreadPoolExecutor(max_workers=UPLOAD_WORKERS) as pool:
            for err in pool.map(one, company.docs):
                if self.cancelled:
                    break
                done += 1
                if err:
                    failed += 1
                    if len(errors) < 3:
                        errors.append(err)
                if done % 20 == 0 or done == len(company.docs):
                    self.emit({"type": "progress", "phase": "upload",
                               "done": done, "total": len(company.docs)})

        for e in errors:
            self.log(f"Upload failed — {e}", "error")
        fmt = ", ".join(f"{n} {ext}" for ext, n in sorted(company.stats["formats"].items()))
        self.log(f"Uploaded {done - failed}/{len(company.docs)} documents "
                 f"in {time.time() - t0:.1f}s ({fmt})", "ok" if not failed else "warn")

    def _ask(self, questions, asker: NexusClient) -> None:
        judge = None
        if self.use_judge:
            try:
                from groq import Groq
                judge = Groq(api_key=read_env_key(self.paths.env_file))
            except Exception as e:
                self.log(f"AI judge unavailable ({e}) — grading with rules only", "warn")

        for i, q in enumerate(questions, 1):
            if self.cancelled:
                self.log("Cancelled", "warn")
                break
            self.emit({"type": "asking", "qid": q.qid, "kind": q.kind, "prompt": q.prompt,
                       "done": i - 1, "total": len(questions)})

            # Groq's free tier is 8k tokens/minute and this corpus is large, so
            # rate limits are routine rather than exceptional. Backing off and
            # retrying is the difference between measuring the app and
            # measuring the API quota.
            ans = asker.ask(q.prompt)
            if ans.error == "rate_limited":
                # Find out which wall we hit before deciding to wait. Retrying a
                # spent daily budget just burns wall-clock and still ends in a
                # column of zeros that libels the app.
                qt = quota.probe(read_env_key(self.paths.env_file))
                if qt.exhausted_for_today:
                    self.emit({"type": "quota", "quota": qt,
                               "remaining_questions": len(questions) - i + 1})
                    for rest in questions[i - 1:]:
                        self.results.append(Result(
                            qid=rest.qid, kind=rest.kind, prompt=rest.prompt,
                            expected=rest.expected, answer="", error="quota_exhausted"))
                    break
                for attempt, wait in enumerate(RATE_LIMIT_BACKOFF, start=1):
                    if ans.error != "rate_limited" or self.cancelled:
                        break
                    self.emit({"type": "retry", "qid": q.qid, "wait": wait, "attempt": attempt})
                    time.sleep(wait)
                    ans = asker.ask(q.prompt)

            rv = rule_verdict(q, ans.text, ans.error)
            jv = judge_verdict(judge, q, ans.text, ans.error) if judge else Verdict(False, "")

            r = Result(qid=q.qid, kind=q.kind, prompt=q.prompt, expected=q.expected,
                       answer=ans.text, sources=ans.sources, mode=ans.mode,
                       elapsed=ans.elapsed, rule_ok=rv.correct, rule_reason=rv.reason,
                       judge_ok=jv.correct if judge else rv.correct,
                       judge_reason=jv.reason, judged=bool(judge), error=ans.error)
            self.results.append(r)
            self.emit({"type": "result", "result": r, "done": i, "total": len(questions)})
            time.sleep(self.delay)

        if judge:
            try:
                judge.close()
            except Exception:
                pass

    # ── Tally ──────────────────────────────────────────────────
    def _finish(self, company, started: float, backend, cancelled: bool = False) -> dict:
        # A question the backend never actually answered — a rate limit that
        # survived every retry, a timeout — measures the API quota, not the
        # app's accuracy. Those are excluded from the percentages and reported
        # as their own number, so the score stays an honest measure of the app
        # and a bad network day can't quietly mark it down.
        scored = [r for r in self.results if not r.error]
        errored = [r for r in self.results if r.error]

        by = defaultdict(lambda: {"n": 0, "rule": 0, "judge": 0, "agree": 0})
        for r in scored:
            b = by[r.kind]
            b["n"] += 1
            b["rule"] += int(r.rule_ok)
            b["judge"] += int(r.judge_ok)
            b["agree"] += int(r.agree)

        table = []
        for kind, label in KIND_LABELS.items():
            b = by.get(kind)
            if not b or not b["n"]:
                continue
            table.append({
                "kind": kind, "label": label, "n": b["n"],
                "rule_pct": round(100 * b["rule"] / b["n"], 1),
                "judge_pct": round(100 * b["judge"] / b["n"], 1),
                "agree_pct": round(100 * b["agree"] / b["n"], 1),
                "judgement": kind in JUDGEMENT_KINDS,
            })

        factual = [r for r in scored if r.kind not in JUDGEMENT_KINDS]
        judgement = [r for r in scored if r.kind in JUDGEMENT_KINDS]
        nf = len(factual) or 1
        # A leak is only a leak if the answer actually came back.
        leaks = [r for r in scored if r.kind == "permission" and not r.rule_ok]

        summary = {
            "company": company.name,
            "company_id": self.company_id,
            "seed": self.seed,
            "total": len(self.results),
            "factual_n": len(factual),
            "factual_rule_pct": round(100 * sum(r.rule_ok for r in factual) / nf, 1),
            "factual_judge_pct": round(100 * sum(r.judge_ok for r in factual) / nf, 1),
            "judgement_n": len(judgement),
            "judgement_rule_pct": (round(100 * sum(r.rule_ok for r in judgement) / len(judgement), 1)
                                   if judgement else None),
            "agree_pct": round(100 * sum(r.agree for r in scored) / (len(scored) or 1), 1),
            "judged": self.use_judge,
            "leaks": len(leaks),
            "permission_n": by["permission"]["n"],
            "errored": len(errored),
            "rate_limited": sum(1 for r in errored if r.error == "rate_limited"),
            "quota_exhausted": sum(1 for r in errored if r.error == "quota_exhausted"),
            "avg_latency": round(sum(r.elapsed for r in self.results) /
                                 (len(self.results) or 1), 1),
            "duration": round(time.time() - started, 1),
            "files": len(company.docs),
            "formats": company.stats["formats"],
            "cancelled": cancelled,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "asker": company.stats["asker_email"],
        }
        payload = {"type": "done", "summary": summary, "table": table, "results": self.results}
        self.emit(payload)
        return payload
