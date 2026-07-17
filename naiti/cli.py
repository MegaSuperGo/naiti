"""
naiti — Nexus AI Tester Internal

  naiti INTERNAL-TESTING-5312          rules-only grading
  naiti -ai INTERNAL-TESTING-5312      AI double-checks only what the rules failed
  naiti --ai-all INTERNAL-TESTING-5312 AI judges every answer
  naiti -api <groq-key>                set the API key for the whole project
  naiti --doctor                       check the environment before testing
"""
from __future__ import annotations

import argparse
import sys
import time

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from . import provision, quota
from .backend import BackendError, app_python, check_backend_deps, port_free
from .config import (
    BACKEND_PORT,
    DEFAULT_COMPANY_ID,
    DEFAULT_SEED,
    TEST_PASSWORD,
    AppNotFound,
    Paths,
    find_app,
    read_env_key,
    write_env_key,
)
from .runner import JUDGEMENT_KINDS, KIND_LABELS, Runner

console = Console()

BANNER = r"""[bold cyan]
                 _ _   _
  _ __   __ _  (_) |_(_)
 | '_ \ / _` | | | __| |
 | | | | (_| | | | |_| |
 |_| |_|\__,_| |_|\__|_|
[/bold cyan][dim]  Nexus AI Tester Internal · v1.0.0[/dim]
"""

KIND_STYLE = {
    "financial_lookup": "cyan",
    "computation": "blue",
    "trend": "magenta",
    "aggregation": "green",
    "permission": "yellow",
    "refusal": "bright_blue",
    "forecast": "bright_magenta",
}


def pct_style(v: float) -> str:
    return "green" if v >= 80 else "yellow" if v >= 50 else "red"


def _short(kind: str) -> str:
    return {
        "financial_lookup": "lookup", "computation": "compute", "trend": "trend",
        "aggregation": "aggregate", "permission": "security", "refusal": "refusal",
        "forecast": "forecast",
    }.get(kind, kind)


# ── Sub-commands ───────────────────────────────────────────────
def cmd_set_api(paths: Paths, key: str) -> int:
    key = key.strip()
    if len(key) < 12:
        console.print("[red]That doesn't look like a valid API key (too short).[/red]")
        return 1

    action = write_env_key(paths.env_file, key, "GROQ_API_KEY")
    masked = f"{key[:6]}…{key[-4:]}"

    console.print()
    console.print(Panel(
        f"[green]GROQ_API_KEY {action}[/green]\n\n"
        f"  Key   [white]{masked}[/white]\n"
        f"  File  [dim]{paths.env_file}[/dim]\n\n"
        f"[dim]This is the key the whole nexus_what backend uses. Restart any running\n"
        f"backend for it to pick up the change.[/dim]",
        title="API key configured", border_style="green", padding=(1, 2)))

    if not key.startswith("gsk_"):
        console.print(
            "\n[yellow]Note:[/yellow] Nexus AI answers via [bold]Groq[/bold], whose keys start "
            "with [cyan]gsk_[/cyan].\n"
            "      This key was saved as-is, but if it's from another provider the backend "
            "won't be able to use it.\n")
    return 0


def cmd_doctor(paths: Paths) -> int:
    console.print(BANNER)
    console.print(Rule("[bold]Environment check[/bold]"))

    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(width=3)
    t.add_column(style="white", width=22)
    t.add_column(style="dim")

    ok = True

    def row(good, label, detail, fatal=True):
        nonlocal ok
        mark = "[green]✓[/green]" if good else ("[red]✗[/red]" if fatal else "[yellow]![/yellow]")
        t.add_row(mark, label, detail)
        if not good and fatal:
            ok = False

    row(True, "Project", str(paths.app))
    row(paths.backend.exists(), "Backend", str(paths.backend))

    try:
        py = app_python(paths)
        row(True, "Python", py)
        missing = check_backend_deps(paths)
        row(not missing, "Dependencies",
            "all backend imports available" if not missing
            else f"missing: {', '.join(missing)} — run: {py} -m pip install -r "
                 f"{paths.backend / 'requirements.txt'}")
    except BackendError as e:
        row(False, "Python", str(e).splitlines()[0])

    key = read_env_key(paths.env_file)
    row(bool(key), "API key",
        f"GROQ_API_KEY set ({key[:6]}…{key[-4:]})" if key
        else "not set — run: naiti -api <your-groq-key>")

    if key:
        q = quota.probe(key)
        if q.exhausted_for_today:
            row(False, "Token budget",
                f"daily limit spent ({q.used:,}/{q.limit:,}) — resets in "
                f"{q.retry_after or 'a few minutes'}")
        elif q.message and not q.ok:
            row(False, "Token budget", q.message[:60])
        else:
            row(True, "Token budget",
                f"key live · {q.tpm_remaining:,}/{q.tpm_limit:,} tokens this minute · "
                f"free tier allows {quota.FREE_TIER_TPD:,}/day")

    row(port_free(BACKEND_PORT), "Port", f"{BACKEND_PORT} available"
        if port_free(BACKEND_PORT) else f"{BACKEND_PORT} is in use — free it or pass --url")
    row(paths.data.exists(), "Data directory", str(paths.data), fatal=False)

    console.print(t)
    console.print()
    if ok:
        console.print(Panel("[green]Ready to test.[/green]  Run: "
                            f"[bold cyan]naiti {DEFAULT_COMPANY_ID}[/bold cyan]",
                            border_style="green", padding=(0, 2)))
    else:
        console.print(Panel("[red]Not ready — fix the items marked ✗ above.[/red]",
                            border_style="red", padding=(0, 2)))
    console.print()
    return 0 if ok else 1


def cmd_list(paths: Paths) -> int:
    rows = provision.list_companies(paths)
    console.print()
    if not rows:
        console.print("  [dim]No companies registered in this project yet.[/dim]\n")
        return 0
    t = Table(title="Companies in this Nexus AI instance", title_justify="left",
              header_style="dim", box=None, padding=(0, 2))
    t.add_column("Company ID", style="cyan")
    t.add_column("Name")
    t.add_column("Users", justify="right")
    t.add_column("Docs", justify="right")
    t.add_column("Created", style="dim")
    t.add_column("Status")
    for r in rows:
        t.add_row(r["id"], r["name"], str(r["users"]),
                  str(r["documents"]) if r["documents"] >= 0 else "—",
                  (r["created_at"] or "")[:10],
                  "[green]active[/green]" if r["active"] else "[red]inactive[/red]")
    console.print(t)
    console.print()
    return 0


def cmd_cleanup(paths: Paths, company_id: str, force: bool) -> int:
    rows = {r["id"]: r for r in provision.list_companies(paths)}
    if company_id not in rows:
        console.print(f"\n  [yellow]No company {company_id} in this instance.[/yellow]\n")
        return 1

    info = rows[company_id]
    console.print()
    console.print(Panel(
        f"About to permanently delete:\n\n"
        f"  Company   [cyan]{company_id}[/cyan] — {info['name']}\n"
        f"  Users     {info['users']}\n"
        f"  Documents {info['documents']}\n"
        f"  Database  [dim]{paths.company_db(company_id)}[/dim]",
        title="[red]Delete company[/red]", border_style="red", padding=(1, 2)))

    if not force:
        console.print()
        reply = console.input("  Type the company ID to confirm: ").strip()
        if reply != company_id:
            console.print("\n  [dim]Cancelled — nothing was deleted.[/dim]\n")
            return 1

    res = provision.purge_company(paths, company_id)
    console.print(f"\n  [green]✓[/green] Removed {company_id} "
                  f"({res['users']} users, {res['chats']} chats, "
                  f"database {'deleted' if res['db_removed'] else 'not found'})\n")
    return 0


def _quota_gate(paths: Paths, args) -> bool:
    """Check the token budget before spending 4 minutes uploading 500 files.

    Groq's free tier is 200k tokens/day against the composer model and a full
    run costs roughly that much, so it is entirely normal to not have enough
    left. Far better to say so up front than to upload everything and then
    stall halfway through with a screen of zeros.
    """
    from .corpus import generate

    n = args.n or len([q for q in generate("PREFLIGHT").questions
                       if not args.only or q.kind == args.only])
    # The judge runs on a different model with its own quota, so it never
    # touches the composer budget this gate is about.
    need = quota.estimate_run_tokens(n)

    q = quota.probe(read_env_key(paths.env_file))

    if q.exhausted_for_today:
        console.print(Panel(
            f"[bold red]Groq's daily token budget is spent.[/bold red]\n\n"
            f"  Limit      {q.limit:,} tokens/day  [dim](free tier)[/dim]\n"
            f"  Used       {q.used:,}\n"
            f"  Resets in  {q.retry_after or 'a few minutes (rolling 24h window)'}\n\n"
            f"[white]Every question would fail, and naiti would report zeros that make the "
            f"app\nlook broken when it is simply out of credit. Nothing was run.[/white]\n\n"
            f"[dim]Options:\n"
            f"  · wait for the window to roll over, then re-run\n"
            f"  · use a different key:  naiti -api <another-groq-key>\n"
            f"  · upgrade the key at https://console.groq.com/settings/billing[/dim]",
            title="[red]Out of tokens[/red]", border_style="red", padding=(1, 2)))
        console.print()
        return False

    # Groq only reports the per-minute budget in headers — never the remaining
    # daily allowance — so this can't be a live check. Compare against the
    # published free-tier daily cap and let the user judge.
    if need > quota.FREE_TIER_TPD:
        fits = max(1, int(quota.FREE_TIER_TPD * 0.85 // 5_000))
        console.print(Panel(
            f"[yellow]A full run is larger than Groq's free daily budget.[/yellow]\n\n"
            f"  This run   ~{need:,} tokens for {n} questions\n"
            f"  Free tier   {quota.FREE_TIER_TPD:,} tokens/day on the composer model\n\n"
            f"[dim]naiti will run anyway, and anything the backend can't answer is "
            f"excluded from\nthe score rather than counted wrong. But for a complete "
            f"picture in one sitting:\n"
            f"  [cyan]naiti {'-ai ' if args.judge_mode != 'off' else ''}-n {fits} {args.company_id or ''}[/cyan]\n"
            f"Ignore this if the key is on a paid tier.[/dim]",
            title="Token budget", border_style="yellow", padding=(1, 2)))
        console.print()

    return True


# ── The test run ───────────────────────────────────────────────
def cmd_run(paths: Paths, args) -> int:
    company_id = (args.company_id or DEFAULT_COMPANY_ID).upper()

    console.print(BANNER)
    mode = {
        "off": "[cyan]rules only[/cyan]",
        "escalate": "[green]rules, AI double-checks the failures[/green]",
        "all": "[green]rules + AI judge on every answer[/green]",
    }[args.judge_mode]
    console.print(
        f"  Project   [dim]{paths.app}[/dim]\n"
        f"  Company   [cyan]{company_id}[/cyan]\n"
        f"  Grading   {mode}"
        + ("  [dim](add -ai to double-check failures)[/dim]" if args.judge_mode == "off" else "")
        + "\n")

    state = {"payload": None, "failed": None}
    results_live: list = []

    progress = Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[white]{task.description}"),
        BarColumn(bar_width=34, complete_style="cyan", finished_style="green"),
        TextColumn("[dim]{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console, transient=True,
    )
    tasks: dict = {}

    def emit(ev: dict) -> None:
        t = ev["type"]

        if t == "log":
            style = {"error": "red", "warn": "yellow", "ok": "green"}.get(ev["level"], "dim")
            prefix = {"error": "✗", "warn": "!", "ok": "✓"}.get(ev["level"], "·")
            console.print(f"  [{style}]{prefix}[/{style}] [dim]{ev['msg']}[/dim]")

        elif t == "company":
            s = ev["stats"]
            fmts = " · ".join(f"{n} {ext}" for ext, n in sorted(s["formats"].items()))
            console.print(Panel(
                f"[bold]{ev['name']}[/bold]  [dim]4 fiscal years · {s['quarters']} quarters "
                f"of audited financials[/dim]\n\n"
                f"  Documents   [white]{s['files']}[/white]  [dim]({fmts})[/dim]\n"
                f"  Restricted  [yellow]{s['restricted']}[/yellow] "
                f"[dim]— confidential, must never surface for {s['asker_email']}[/dim]\n"
                f"  Questions   [white]{ev['questions']}[/white]",
                border_style="blue", padding=(1, 2)))
            console.print()

        elif t == "phase":
            if ev.get("total"):
                tasks[ev["phase"]] = progress.add_task(ev["label"], total=ev["total"])

        elif t == "progress":
            if ev["phase"] in tasks:
                progress.update(tasks[ev["phase"]], completed=ev["done"])

        elif t == "asking":
            if "ask" in tasks:
                progress.update(tasks["ask"], completed=ev["done"],
                                description=f"Asking · [dim]{ev['prompt'][:44]}[/dim]")

        elif t == "retry":
            progress.console.print(
                f"  [yellow]![/yellow] [dim]{ev['qid']}: Groq rate limit — waiting "
                f"{ev['wait']}s and retrying (attempt {ev['attempt']})[/dim]")

        elif t == "result":
            r = ev["result"]
            results_live.append(r)
            if "ask" in tasks:
                progress.update(tasks["ask"], completed=ev["done"])
            kind_txt = f"[{KIND_STYLE.get(r.kind, 'white')}]{_short(r.kind):9}[/]"

            if r.error:
                # Not scored — the app never got to answer.
                progress.console.print(
                    f"  [yellow]~~[/yellow] {kind_txt} [dim]{r.prompt[:56]}[/dim]"
                    f"  [yellow]not scored[/yellow]")
                progress.console.print(
                    f"        [dim]{'Groq rate limit — survived all retries' if r.error == 'rate_limited' else r.error[:70]}[/dim]")
                return

            leak = r.kind == "permission" and not r.final_ok
            # Show the rule verdict, then how review changed it.
            mark = "[green]✓[/green]" if r.final_ok else "[red]✗[/red]"
            line = f"  {mark} {kind_txt} [white]{r.prompt[:56]}[/white]"
            if r.overturned:
                line += "  [green]← AI rescued[/green]"
            elif r.judged and r.kind == "permission" and not r.rule_ok:
                line += "  [dim](AI checked)[/dim]"
            elif r.judged and not r.final_ok:
                line += "  [dim](AI agreed)[/dim]"
            if leak:
                line += "  [bold red]← LEAK[/bold red]"
            progress.console.print(line)
            if not r.final_ok and not leak:
                progress.console.print(f"        [dim]{r.rule_reason[:76]}[/dim]")

        elif t == "seeded":
            console.print()
            console.print(Panel(
                f"[green]Company seeded and left in place — no questions were asked.[/green]\n\n"
                f"  Company ID  [cyan]{ev['company_id']}[/cyan]\n"
                f"  Password    [white]{ev['password']}[/white]  [dim](all accounts)[/dim]\n"
                f"  Accounts    [dim]{', '.join(ev['users'])}[/dim]\n"
                f"  Documents   {ev['files']}\n\n"
                f"[dim]Sign in at the frontend and explore it by hand.\n"
                f"Remove it later with:  naiti --cleanup {ev['company_id']}[/dim]",
                title="Seeded", border_style="green", padding=(1, 2)))

        elif t == "done":
            state["payload"] = ev

        elif t == "failed":
            state["failed"] = ev["error"]

    if args.only and args.only not in KIND_LABELS:
        console.print(f"  [red]Unknown category {args.only!r}.[/red] "
                      f"Choose from: {', '.join(KIND_LABELS)}\n")
        return 1

    if not _quota_gate(paths, args):
        return 1

    runner = Runner(
        paths, company_id, emit, seed=args.seed, judge_mode=args.judge_mode,
        question_limit=args.n, url=args.url, keep=args.keep,
        fresh=not args.no_fresh, upload_only=args.seed_only, only=args.only,
        delay=args.delay,
    )

    try:
        with progress:
            runner.run()
    except KeyboardInterrupt:
        runner.cancel()
        console.print("\n  [yellow]Interrupted — cleaning up…[/yellow]\n")
        return 130

    if state["failed"]:
        console.print()
        console.print(Panel(f"[red]{state['failed']}[/red]", title="[red]Run failed[/red]",
                            border_style="red", padding=(1, 2)))
        console.print()
        return 1

    if args.seed_only:
        return 0

    payload = state["payload"]
    if not payload:
        return 1

    _render_results(payload, args)

    if args.json:
        _write_json(payload, args.json)
    if args.report:
        from .report import write_html
        write_html(payload, args.report)
        console.print(f"  [dim]HTML report written to {args.report}[/dim]\n")

    return 2 if payload["summary"]["leaks"] else 0


def _render_results(payload: dict, args) -> None:
    s, table = payload["summary"], payload["table"]
    judged = s["judge_mode"] != "off"

    console.print()
    console.print(Rule("[bold]Results[/bold]"))
    console.print()

    # When the judge ran, show both the raw rule score and the reviewed score
    # side by side, so the effect of the AI double-check is visible rather than
    # hidden inside a single number.
    fixed = 56 if judged else 46
    bar_w = max(10, min(24, console.width - fixed))

    def bar(v: float) -> Text:
        filled = round(bar_w * v / 100)
        return Text("█" * filled + "░" * (bar_w - filled),
                    style=pct_style(v) if filled else "dim")

    def score_cell(pct: float, bold: bool = False) -> Text:
        style = pct_style(pct)
        return Text(f"{pct}%", style=f"bold {style}" if bold else style)

    t = Table(box=None, padding=(0, 1), header_style="dim")
    t.add_column("Category", width=23, no_wrap=True)
    t.add_column("Asked", justify="right", width=5)
    t.add_column("Rules", justify="right", width=6)
    if judged:
        t.add_column("Reviewed", justify="right", width=8)
    t.add_column("", width=bar_w)

    for row in table:
        if row["judgement"]:
            continue
        cells = [row["label"], str(row["n"]), score_cell(row["rule_pct"])]
        if judged:
            cells.append(score_cell(row["final_pct"]))
        cells.append(bar(row["final_pct"] if judged else row["rule_pct"]))
        t.add_row(*cells)

    cells = [Text("Factual overall", style="bold"), Text(str(s["factual_n"]), style="bold"),
             score_cell(s["factual_rule_pct"], bold=True)]
    if judged:
        cells.append(score_cell(s["factual_final_pct"], bold=True))
    cells.append(bar(s["factual_final_pct"] if judged else s["factual_rule_pct"]))
    t.add_row(*cells)
    console.print(t)

    # Judgement-call categories, scored separately and clearly labelled.
    jrows = [r for r in table if r["judgement"]]
    if jrows:
        console.print()
        jt = Table(box=None, padding=(0, 1), header_style="dim")
        jt.add_column("Judgement call", width=23, no_wrap=True)
        jt.add_column("Asked", justify="right", width=5)
        jt.add_column("Grounded", justify="right", width=6)
        if judged:
            jt.add_column("Reviewed", justify="right", width=8)
        jt.add_column("", width=bar_w)
        for row in jrows:
            cells = [row["label"], str(row["n"]), score_cell(row["rule_pct"])]
            if judged:
                cells.append(score_cell(row["final_pct"]))
            cells.append(bar(row["final_pct"] if judged else row["rule_pct"]))
            jt.add_row(*cells)
        console.print(jt)
        console.print("  [dim]Forecasts have no single right answer. 'Grounded' means the "
                      "projection was\n  anchored to the real historicals and inside a "
                      "defensible band — it is scored\n  separately so it never moves the "
                      "factual number.[/dim]")

    console.print()
    meta = (f"  [dim]{s['files']} documents · asked as {s['asker']} · "
            f"{s['avg_latency']}s average per question · {s['duration']}s total[/dim]")
    if judged:
        if s["judge_mode"] == "escalate":
            meta += (f"\n  [dim]AI double-checked {s['judged_n']} answer(s) the rules "
                     f"flagged; it rescued {s['overturned_n']} that were correct but "
                     f"worded differently.[/dim]")
        else:
            meta += (f"\n  [dim]AI judged all {s['judged_n']} answers; it rescued "
                     f"{s['overturned_n']} the rules had marked wrong.[/dim]")
    console.print(meta)

    if s.get("errored"):
        console.print(
            f"\n  [yellow]{s['errored']} question(s) excluded from the score[/yellow] "
            f"[dim]— the backend never returned an answer\n"
            f"  ({s['rate_limited']} hit the Groq rate limit through every retry). "
            f"These are an API quota\n  limit, not an accuracy failure, so they are not "
            f"counted either way. Re-run when\n  the quota resets for a complete "
            f"picture.[/dim]")
    console.print()

    if s["permission_n"]:
        if s["leaks"]:
            console.print(Panel(
                f"[bold red]{s['leaks']} of {s['permission_n']} confidential documents "
                f"leaked[/bold red]\n\nA user without permission was shown restricted "
                f"content. This is a security defect, not an accuracy one.",
                border_style="red", padding=(1, 2)))
        else:
            console.print(Panel(
                f"[bold green]No confidential content leaked.[/bold green]  All "
                f"{s['permission_n']} permission probes were correctly refused by a "
                f"user without access.",
                border_style="green", padding=(1, 2)))
        console.print()

    if not judged:
        console.print("  [dim]Graded with deterministic rules only. Run with [cyan]-ai[/cyan] "
                      "to add the LLM judge,\n  which also credits correct answers that are "
                      "worded unusually.[/dim]\n")


def _write_json(payload: dict, path: str) -> None:
    import json
    from dataclasses import asdict

    out = {
        "summary": payload["summary"],
        "table": payload["table"],
        "results": [asdict(r) for r in payload["results"]],
    }
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    console.print(f"  [dim]JSON written to {path}[/dim]")


# ── Entry point ────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="naiti",
        description="Nexus AI Tester Internal — black-box answer-quality benchmark.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  naiti INTERNAL-TESTING-5312          run with deterministic rule grading
  naiti -ai INTERNAL-TESTING-5312      AI double-checks the answers the rules failed
  naiti --ai-all INTERNAL-TESTING-5312 AI judges every answer (more tokens)
  naiti -api gsk_your_key_here         set the project's Groq API key
  naiti --doctor                       check the environment is ready
  naiti --seed-only DEMO-1234          load the fake company, ask nothing, leave it
  naiti --list                         list companies in this instance
  naiti --cleanup DEMO-1234            delete a test company
  naiti -ai -n 14 --report out.html    quick run with an HTML report
""")

    p.add_argument("company_id", nargs="?", help=f"company ID to test (default {DEFAULT_COMPANY_ID})")
    p.add_argument("-ai", "--ai", action="store_true",
                   help="use the AI judge to double-check answers the rules failed (saves tokens)")
    p.add_argument("--ai-all", action="store_true",
                   help="run the AI judge on every answer, not just the failures")
    p.add_argument("-api", "--api", metavar="KEY", help="set GROQ_API_KEY for the whole project")

    p.add_argument("-n", type=int, metavar="N", help="limit questions (spread across all categories)")
    p.add_argument("--only", metavar="KIND", help=f"test one category only ({', '.join(KIND_LABELS)})")
    p.add_argument("--delay", type=float, metavar="SEC",
                   help="seconds between questions (raise if you hit Groq rate limits)")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED, help="company generation seed")
    p.add_argument("--url", metavar="URL", help="test a backend already running at this URL")
    p.add_argument("--app", metavar="PATH", help="path to the nexus_what project")

    p.add_argument("--keep", action="store_true", help="leave the test company in place afterwards")
    p.add_argument("--no-fresh", action="store_true", help="keep documents from a previous run")
    p.add_argument("--seed-only", action="store_true",
                   help="provision and upload, ask nothing, leave it for manual exploration")

    p.add_argument("--doctor", action="store_true", help="check the environment and exit")
    p.add_argument("--list", action="store_true", help="list companies in this instance")
    p.add_argument("--cleanup", metavar="ID", help="delete a test company and its documents")
    p.add_argument("--force", action="store_true", help="skip the cleanup confirmation")

    p.add_argument("--json", metavar="PATH", help="write full results as JSON")
    p.add_argument("--report", metavar="PATH", help="write a shareable HTML report")
    p.add_argument("--version", action="version", version="naiti 1.0.0")
    return p


def main() -> int:
    args = build_parser().parse_args()

    # off → rules only · escalate → judge only the rule-failures (and every
    # security probe) · all → judge everything. --ai-all implies the judge.
    args.judge_mode = "all" if args.ai_all else "escalate" if args.ai else "off"

    try:
        paths = Paths(find_app(args.app))
    except AppNotFound as e:
        console.print(f"\n[red]{e}[/red]\n")
        return 1

    try:
        if args.api:
            return cmd_set_api(paths, args.api)
        if args.doctor:
            return cmd_doctor(paths)
        if args.list:
            return cmd_list(paths)
        if args.cleanup:
            return cmd_cleanup(paths, args.cleanup.upper(), args.force)
        return cmd_run(paths, args)
    except KeyboardInterrupt:
        console.print("\n  [yellow]Interrupted.[/yellow]\n")
        return 130
    except Exception as e:
        console.print(f"\n[red]{type(e).__name__}: {e}[/red]\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
