"""Render evals/results.json into three SVGs (one per metric pair). stdlib only.

Each chart shows a metric next to its counter-metric (KANON.md), same mode colours everywhere:
none=blue, flag=orange, graph=aqua (fixed slot order, validated with the dataviz palette script).
Aqua is under 3:1 against the light surface, so every bar carries its value as visible text.
Rejected: matplotlib (a dependency for three bar charts); one combined chart (a pair read apart
loses the point that the metric and its counter-metric must be read together).
"""
import json
from pathlib import Path

OUT = Path(__file__).parent / "charts"
MODES = ["none", "flag", "graph"]
STYLE = """<style>
:root{--bg:#fcfcfb;--t1:#0b0b0b;--t2:#52514e;--grid:#e4e3df;--none:#2a78d6;--flag:#eb6834;--graph:#1baf7a}
@media (prefers-color-scheme:dark){:root{--bg:#1a1a19;--t1:#f0efec;--t2:#c3c2b7;--grid:#34332f;--none:#3987e5;--flag:#d95926;--graph:#199e70}}
text{font:12px system-ui,sans-serif;fill:var(--t2)} .t{font-weight:600;font-size:14px;fill:var(--t1)} .v{fill:var(--t1);font-weight:600}
</style>"""


def panel(x0, title, note, vals, fmt, top):
    """One bar panel: 3 bars, baseline at y=190, scale 0..top."""
    out = [f'<text class="t" x="{x0}" y="46">{title}</text><text x="{x0}" y="64">{note}</text>',
           f'<line x1="{x0}" x2="{x0+250}" y1="190" y2="190" stroke="var(--grid)"/>']
    for i, m in enumerate(MODES):
        v = vals[m]
        h = 0 if v is None else 88 * v / top
        x = x0 + 12 + i * 80
        if v:
            out.append(f'<path d="M{x},190 V{190-h+4} Q{x},{190-h} {x+4},{190-h} H{x+44} Q{x+48},{190-h} {x+48},{190-h+4} V190 Z" fill="var(--{m})"/>')
        out.append(f'<text class="v" x="{x+24}" y="{190-h-6}" text-anchor="middle">{"n/a" if v is None else fmt(v)}</text>')
        out.append(f'<text x="{x+24}" y="208" text-anchor="middle">{m}</text>')
    return "\n".join(out)


def chart(name, title, left, right):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 230" role="img" aria-label="{title}">{STYLE}'
           f'<rect width="600" height="230" fill="var(--bg)"/><text class="t" x="20" y="22">{title}</text>'
           f'{panel(20, *left)}{panel(320, *right)}</svg>')
    (OUT / f"{name}.svg").write_text(svg)


def main():
    d = json.load(open(Path(__file__).parent / "results.json"))
    g = {m: d["generation"][m]["summary"] for m in MODES}
    r = {m: d["retrieval"][m]["summary"] for m in MODES}
    pct = lambda v: f"{100*v:.0f}%"
    chart("generation", "Generation: superseded citations vs answer rate (19 qa questions)",
          ("Superseded-citation rate", "lower is better", {m: g[m]["superseded_citation_rate"] for m in MODES}, pct, 1),
          ("Answer rate", "higher is NOT better on its own", {m: g[m]["answer_rate"] for m in MODES}, pct, 1))
    chart("retrieval", "Retrieval, no LLM: recall@8 vs superseded chunks retrieved",
          ("Recall@8", "higher is better", {m: r[m]["recall_at_k"] for m in MODES}, lambda v: f"{v:.2f}", 1),
          ("Superseded-chunk rate", "lower is better", {m: r[m]["superseded_chunk_rate"] for m in MODES}, pct, 1))
    s = d["resolver"]["summary"]
    chart("resolver", "Resolver, store level (13 questions): who knows what governed",
          ("Correct", "none has no authority data (n/a)", {"none": None, "flag": s["flag_correct"], "graph": s["graph_correct"]}, lambda v: f"{v}/13", 13),
          ("Wrong or unresolved", "flag answers from today's status only", {"none": None, "flag": 13 - s["flag_correct"], "graph": 13 - s["graph_correct"]}, lambda v: f"{v}/13", 13))


if __name__ == "__main__":
    main()
