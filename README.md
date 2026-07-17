# naiti — Nexus AI Tester Internal

A black-box answer-quality benchmark for the Nexus AI knowledge base.

`naiti` builds a fake company with four years of consistent financials, spreads
it across 500 real Office documents, uploads them through the app's own API as
six different staff accounts, then asks the AI questions it already knows the
answers to and scores what comes back.

```bash
naiti INTERNAL-TESTING-5312        # deterministic rule grading
naiti -ai INTERNAL-TESTING-5312    # AI double-checks the answers the rules failed
naiti -api gsk_your_key_here       # set the project's API key
naiti --doctor                     # check everything is ready first
```

---

## Install

```bash
brew tap felixtyx/tap
brew install naiti
```

or, without Homebrew:

```bash
pipx install git+https://github.com/felixtyx/naiti
```

`naiti` finds the `nexus_what` project automatically if you run it from inside
or next to the checkout. Otherwise:

```bash
naiti --app /path/to/nexus_what ...
export NAITI_APP=/path/to/nexus_what     # or set it once
```

## First run

```bash
naiti -api gsk_...        # 1. point the project at your own Groq key
naiti --doctor            # 2. confirm the environment is ready
naiti -ai -n 14           # 3. a quick run across every category
```

`--doctor` checks the project layout, the backend's Python and dependencies,
the API key, the live token budget, and whether port 8123 is free. Fix anything
it marks ✗ before testing.

## What it does to your project

`naiti` provisions a **normal tenant** through the app's own `database.py` and
`auth_utils.py` — the same path `admin.py` uses when an operator creates a
company by hand. The test company gets its own `data/<ID>.db`, exactly like any
other customer, and is **deleted automatically when the run finishes**. Pass
`--keep` to leave it in place, or clean up later with `naiti --cleanup <ID>`.

It starts the backend itself on **port 8123**, never 8000 or 8080, so your own
dev instance can keep running alongside a test. The app's source is never
modified — the only file `naiti` ever writes to is `.env`, and only when you
run `-api`.

## The token budget — read this

The app sends up to 12k characters of retrieved context plus a 2.2k completion
allowance per question, and Groq bills the allowance against your limit. That
works out to roughly **5k tokens per question**:

| Run | Approx. tokens |
|---|---|
| `naiti -n 14` | ~78,000 |
| `naiti -ai -n 30` | ~168,000 |
| `naiti -ai` (full, 43 questions) | ~241,000 |

Groq's **free tier allows 200,000 tokens/day** on `openai/gpt-oss-120b` — so a
full `-ai` run does not fit in one day. `naiti` warns you before it starts and,
if the daily budget is already spent, refuses to run rather than producing a
screen of zeros that make the app look broken.

Anything the backend genuinely cannot answer — a rate limit that survives every
retry — is **excluded from the score**, not counted as wrong. An API quota is
not an accuracy failure. The count is always reported.

## The fake company

**Meridian Dynamics**, an industrial-IoT firm. Every figure in every document
traces back to one deterministic model, so a number quoted in a board minute,
a quarterly report, an investor deck and a spreadsheet is always the same
number. Change `--seed` for a different company; the same seed always rebuilds
the identical corpus and ground truth.

| Format | Documents |
|---|---|
| `.docx` | quarterly + annual reports, board minutes, contracts, QBRs |
| `.xlsx` | P&L models, segment revenue, headcount plans |
| `.pptx` | investor updates |
| `.csv` | pipeline exports, regional revenue, invoices, KPIs |
| `.txt` | budget memos, ops reviews, email threads, weekly flashes |

Six documents are marked confidential and restricted to the exec accounts.
Questions are asked as `analyst@meridian.test`, an ordinary member, so the
permission probes are real.

## What it measures

| Category | Probes |
|---|---|
| **Financial lookup** | One document holds the figure. Baseline retrieval. |
| **Computation** | Needs figures from two or more documents combined. |
| **Trend analysis** | Direction plus grounding across four years. |
| **Aggregation** | Summing or ranking across segments and regions. |
| **Permission leakage** | Asks a non-permitted user about confidential files. Must refuse. |
| **Refusal** | Asks about facts that exist nowhere. Must not invent. |
| **Forecasting** | *Scored separately.* See below. |

`-n N` keeps every category represented rather than truncating, so a short run
still exercises the security probes.

### Forecasting is scored separately, on purpose

"What could FY2026 revenue be?" has no single right answer, so it is graded on
**grounding**: did the projection anchor to the real historicals and land in a
defensible band derived from actual trend? A reasoned range passes; a number
from thin air fails.

It is reported in its own table and kept out of the factual headline, so a
judgement call can never quietly move the accuracy number in either direction.

## Two graders — and how they save tokens

- **Rules** — `naiti` computed every figure, so it checks the answer against
  the truth numerically, with tolerance. `EUR 18.4M` and `18,412,000` are the
  same answer. Free, instant, reproducible, and strict.
- **AI judge** — an LLM grades question + truth + answer, and catches correct
  answers the strict rules marked wrong for being worded unusually.

**`-ai` runs the judge only where it earns its keep.** The rules are the first
pass; the judge is a second opinion, and it only looks at:

- **answers the rules failed** — the strict numeric check produces false
  negatives (a right answer phrased oddly), and that is exactly what the judge
  rescues. A rule *pass* on a figure naiti computed itself needs no second
  opinion, so it is left alone. This is where the tokens are saved — a typical
  run judges only ~20–30% of questions instead of all of them.
- **every security probe**, whatever the rules said. Here the unreliable
  direction is inverted: the rules only catch a secret quoted verbatim, so a
  paraphrased leak ("about four point two million") would slip through a rule
  pass. Six probes cost almost nothing; a missed leak costs everything.

The results table shows **Rules** and **Reviewed** side by side, so you can see
what the judge changed. `--ai-all` judges every answer if you want the
exhaustive cross-check.

> **The judge uses a different model** (`gpt-oss-20b`) with a **separate** Groq
> quota from the app's answering model (`gpt-oss-120b`). So `-ai` mainly saves
> time and the judge's own quota — it does *not* reduce pressure on the daily
> budget that actually runs out, which is spent entirely by the app answering
> questions. Fewer questions (`-n`) is the only thing that reduces that.

## Useful flags

| Flag | Does |
|---|---|
| `-ai` | add the LLM judge |
| `-n N` | limit questions, spread across all categories |
| `--only KIND` | test one category (`permission`, `forecast`, …) |
| `--seed-only` | upload the company and stop — explore it by hand in the UI |
| `--keep` | leave the test company in place afterwards |
| `--list` | list companies in this instance |
| `--cleanup ID` | delete a test company and its documents |
| `--report out.html` | shareable HTML report |
| `--json out.json` | full machine-readable results |
| `--delay SEC` | slow down if you hit rate limits |
| `--url URL` | test a backend already running elsewhere |

`--seed-only` is the quickest way to *see* the product: it loads 500 documents
and six logins (password `NaitiTest!2026`), then leaves them for you to click
around in.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | ran, nothing leaked |
| `1` | could not run (bad environment, no tokens, port in use) |
| `2` | **a confidential document leaked** |

CI-safe: a leak fails the build.

## Known limitations

- A full `-ai` run exceeds Groq's free daily budget. Use `-n 30` or a paid key.
- The app's system prompt (`chat.py`) instructs the model to answer *only* from
  retrieved excerpts and not to speculate. That is why some forecasting
  questions are declined rather than answered — the model is obeying its
  instructions, not failing.
- `GET /api/files/` is not permission-filtered: every user can list all
  document names, including restricted ones. Contents are correctly protected;
  filenames are not.
