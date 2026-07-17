"""
quota.py — understanding Groq's 429s.

The backend catches upstream errors and rewrites them into a friendly sentence
("The AI service rate limit was hit…"), so by the time naiti sees an answer the
useful detail is gone. naiti holds the same API key, so it asks Groq directly
what kind of limit was hit.

The distinction matters enormously:

  TPM (tokens per minute) — transient. Wait a beat and carry on.
  TPD (tokens per day)    — the daily budget is spent. No amount of retrying
                            helps; every remaining question would fail and the
                            run would report a pile of zeros that look like the
                            app is broken when it is simply out of credit.

A full naiti run costs roughly 150k–200k tokens against the composer model, and
Groq's free tier allows 200k/day — so about one full run per day. That is worth
telling someone *before* they burn it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

COMPOSER_MODEL = "openai/gpt-oss-120b"

# "on tokens per day (TPD): Limit 200000, Used 198052, Requested 2571.
#  Please try again in 4m29.136s."
_KIND = re.compile(r"tokens per (day|minute)\s*\((TPD|TPM)\)", re.I)
_LIMIT = re.compile(r"Limit\s+(\d+)", re.I)
_USED = re.compile(r"Used\s+(\d+)", re.I)
_RETRY = re.compile(r"try again in\s+([0-9hms.]+)", re.I)


# Groq's free tier for the composer model. Their response headers only ever
# report the *per-minute* budget, never the daily one, so the daily figure
# cannot be read live — it is only revealed in the body of a TPD 429. This
# constant is what the API itself reported when the wall was hit.
FREE_TIER_TPD = 200_000


@dataclass
class Quota:
    ok: bool
    kind: str = ""          # "TPD" | "TPM" | "" — only set when a 429 was seen
    limit: int = 0          # from a 429 body: the limit that was breached
    used: int = 0
    retry_after: str = ""
    message: str = ""
    # From response headers on a successful call. These are per-MINUTE figures;
    # Groq does not expose remaining daily tokens anywhere.
    tpm_limit: int = 0
    tpm_remaining: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def exhausted_for_today(self) -> bool:
        return self.kind.upper() == "TPD"


def parse_429(text: str) -> Quota:
    m = _KIND.search(text)
    kind = (m.group(2).upper() if m else "")
    lim = _LIMIT.search(text)
    used = _USED.search(text)
    retry = _RETRY.search(text)
    return Quota(
        ok=False, kind=kind,
        limit=int(lim.group(1)) if lim else 0,
        used=int(used.group(1)) if used else 0,
        retry_after=retry.group(1) if retry else "",
        message=text[:400],
    )


def _int(headers, name: str) -> int:
    try:
        return int(float(headers.get(name, "") or 0))
    except (TypeError, ValueError):
        return 0


def probe(api_key: str, model: str = COMPOSER_MODEL) -> Quota:
    """Ask Groq for the current state of play with a deliberately tiny request.

    On success Groq reports the remaining daily budget in response headers, so
    one ~1-token call reveals whether a full run can actually finish. On a 429
    the body carries the limit/used figures instead. Either way we come back
    with real numbers rather than a guess.
    """
    try:
        from groq import Groq
    except Exception as e:
        return Quota(ok=True, message=f"groq sdk unavailable: {e}")

    try:
        client = Groq(api_key=api_key)
        raw = client.chat.completions.with_raw_response.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_completion_tokens=1,
        )
        h = raw.headers
        return Quota(
            ok=True, kind="",
            tpm_limit=_int(h, "x-ratelimit-limit-tokens"),
            tpm_remaining=_int(h, "x-ratelimit-remaining-tokens"),
            retry_after=h.get("x-ratelimit-reset-tokens", "") or "",
        )
    except Exception as e:
        text = str(e)
        if "429" in text or "rate_limit" in text.lower():
            return parse_429(text)
        return Quota(ok=True, message=text[:200])   # not a quota problem


def estimate_run_tokens(n_questions: int, judge: bool) -> int:
    """Rough cost of a run.

    The app sends up to CONTEXT_CHAR_BUDGET (12k chars ≈ 3k tokens) of excerpts
    plus a 2.2k completion allowance, and Groq bills the allowance against the
    limit — so ~5k tokens a question, plus ~0.6k if the judge is grading.
    """
    per = 5_000 + (600 if judge else 0)
    return n_questions * per
