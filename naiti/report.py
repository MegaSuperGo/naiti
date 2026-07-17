"""report.py — a single-file HTML report a coach can open or forward."""
from __future__ import annotations

import html
from dataclasses import asdict


def _bar(pct: float) -> str:
    color = "#3ecf8e" if pct >= 80 else "#ffb454" if pct >= 50 else "#ff6b6b"
    return (f'<span class="bar"><i style="width:{pct}%;background:{color}"></i></span>')


def write_html(payload: dict, path: str) -> None:
    s, table = payload["summary"], payload["table"]
    rows = [asdict(r) if not isinstance(r, dict) else r for r in payload["results"]]
    judged = s["judged"]

    def color(p): return "#3ecf8e" if p >= 80 else "#ffb454" if p >= 50 else "#ff6b6b"

    trs = []
    for row in table:
        if row["judgement"]:
            continue
        j = f'<td style="color:{color(row["judge_pct"])}">{row["judge_pct"]}%</td>' if judged else ""
        trs.append(
            f'<tr><td>{html.escape(row["label"])}</td><td class=n>{row["n"]}</td>'
            f'<td style="color:{color(row["rule_pct"])}">{row["rule_pct"]}%</td>{j}'
            f'<td>{_bar(row["rule_pct"])}</td></tr>')
    jt = f'<td style="color:{color(s["factual_judge_pct"])}">{s["factual_judge_pct"]}%</td>' if judged else ""
    trs.append(
        f'<tr class=total><td>Factual overall</td><td class=n>{s["factual_n"]}</td>'
        f'<td style="color:{color(s["factual_rule_pct"])}">{s["factual_rule_pct"]}%</td>{jt}'
        f'<td>{_bar(s["factual_rule_pct"])}</td></tr>')

    jrows = "".join(
        f'<tr><td>{html.escape(r["label"])}</td><td class=n>{r["n"]}</td>'
        f'<td style="color:{color(r["rule_pct"])}">{r["rule_pct"]}%</td>'
        + (f'<td style="color:{color(r["judge_pct"])}">{r["judge_pct"]}%</td>' if judged else "")
        + f'<td>{_bar(r["rule_pct"])}</td></tr>'
        for r in table if r["judgement"])

    qrows = []
    for r in rows:
        ok = "✓" if r["rule_ok"] else "✗"
        cls = "ok" if r["rule_ok"] else "bad"
        qrows.append(
            f'<details class="{cls}"><summary><b class="{cls}">{ok}</b> '
            f'<span class=tag>{html.escape(r["kind"])}</span> {html.escape(r["prompt"])}</summary>'
            f'<div class=d><b>Expected</b><p>{html.escape(str(r["expected"]))}</p>'
            f'<b>Answer</b><p>{html.escape(r["answer"] or "(empty)")}</p>'
            f'<b>Rule verdict</b><p>{html.escape(r["rule_reason"])}</p>'
            + (f'<b>AI judge</b><p>{html.escape(r["judge_reason"])}</p>' if judged else "")
            + f'<b>Sources cited</b><p>{html.escape(", ".join(r["sources"]) or "none")}</p>'
            f'</div></details>')

    leak_banner = (
        f'<div class="banner err"><b>{s["leaks"]} of {s["permission_n"]} confidential documents '
        f'leaked.</b> A user without permission was shown restricted content.</div>'
        if s["leaks"] else
        f'<div class="banner ok"><b>No confidential content leaked.</b> All '
        f'{s["permission_n"]} permission probes were correctly refused.</div>')

    doc = f"""<!doctype html><meta charset=utf-8>
<title>naiti report — {html.escape(s["company_id"])}</title>
<style>
:root{{color-scheme:dark}}
body{{background:#0b0e14;color:#e6ebf5;font:14px/1.6 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:960px;margin:0 auto;padding:40px 22px}}
h1{{font-size:21px;margin:0}} h2{{font-size:12px;text-transform:uppercase;letter-spacing:.8px;color:#5a6683;margin:30px 0 10px}}
.sub{{color:#8794ad;font-size:13px;margin:4px 0 22px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:20px}}
.stat{{background:#121722;border:1px solid #232c3d;border-radius:10px;padding:12px 14px}}
.stat .k{{font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:#5a6683}}
.stat .v{{font-size:20px;font-weight:650;margin-top:3px}}
table{{width:100%;border-collapse:collapse;font-size:13px;background:#121722;border:1px solid #232c3d;border-radius:10px;overflow:hidden}}
th{{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:#5a6683;padding:10px;border-bottom:1px solid #232c3d}}
td{{padding:10px;border-bottom:1px solid rgba(35,44,61,.5)}} td.n{{text-align:right}}
tr.total td{{font-weight:700;background:rgba(91,140,255,.06)}}
.bar{{display:block;height:7px;background:#0b0e14;border:1px solid #232c3d;border-radius:4px;overflow:hidden;min-width:120px}}
.bar i{{display:block;height:100%}}
.banner{{border-radius:9px;padding:12px 14px;margin:18px 0;font-size:13px}}
.banner.ok{{background:rgba(62,207,142,.08);border:1px solid rgba(62,207,142,.3);color:#a7f3d0}}
.banner.err{{background:rgba(255,107,107,.09);border:1px solid rgba(255,107,107,.32);color:#ffc9c9}}
details{{background:#121722;border:1px solid #232c3d;border-radius:8px;padding:9px 12px;margin-bottom:6px}}
details.bad{{border-color:rgba(255,107,107,.3)}}
summary{{cursor:pointer;list-style:none}} b.ok{{color:#3ecf8e}} b.bad{{color:#ff6b6b}}
.tag{{font:10px ui-monospace,monospace;text-transform:uppercase;background:rgba(135,148,173,.12);color:#8794ad;padding:2px 6px;border-radius:4px;margin-right:6px}}
.d{{margin-top:10px;padding:10px;background:#0b0e14;border-radius:6px;font-size:12.5px;color:#8794ad}}
.d b{{color:#e6ebf5;font-size:10px;text-transform:uppercase;letter-spacing:.5px;display:block;margin-top:9px}}
.d b:first-child{{margin-top:0}} .d p{{margin:3px 0;white-space:pre-wrap}}
.note{{color:#5a6683;font-size:12px;margin-top:8px}}
</style>
<h1>naiti — Nexus AI answer quality</h1>
<p class=sub>{html.escape(s["company"])} · {html.escape(s["company_id"])} ·
{s["files"]} documents · asked as {html.escape(s["asker"])} · {html.escape(s["finished_at"])}</p>
<div class=stats>
<div class=stat><div class=k>Factual accuracy</div><div class=v style="color:{color(s['factual_rule_pct'])}">{s["factual_rule_pct"]}%</div></div>
<div class=stat><div class=k>Questions</div><div class=v>{s["total"]}</div></div>
<div class=stat><div class=k>Leaks</div><div class=v style="color:{'#ff6b6b' if s['leaks'] else '#3ecf8e'}">{s["leaks"]}</div></div>
<div class=stat><div class=k>Avg latency</div><div class=v>{s["avg_latency"]}s</div></div>
<div class=stat><div class=k>Duration</div><div class=v>{s["duration"]}s</div></div>
</div>
{leak_banner}
<h2>Accuracy by category</h2>
<table><tr><th>Category</th><th class=n>Asked</th><th>Rules</th>{'<th>AI judge</th>' if judged else ''}<th></th></tr>
{''.join(trs)}</table>
{f'<h2>Judgement calls</h2><table><tr><th>Category</th><th class=n>Asked</th><th>Grounded</th>{"<th>AI judge</th>" if judged else ""}<th></th></tr>{jrows}</table><p class=note>Forecasts have no single right answer. &ldquo;Grounded&rdquo; means the projection was anchored to the real historicals and inside a defensible band derived from actual trend. Scored separately so it never moves the factual number.</p>' if jrows else ''}
<h2>Every question</h2>
{''.join(qrows)}
<p class=note>Generated by naiti {html.escape(str(s.get("seed", "")))} · deterministic seed, reproducible run.</p>
"""
    with open(path, "w") as f:
        f.write(doc)
