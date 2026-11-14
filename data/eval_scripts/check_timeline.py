"""10 CFR 50.59 intervals: contiguous, non-overlapping, and anchored to FR effective dates."""
import json, sys
from pathlib import Path
rows = [json.loads(l) for l in (Path(sys.argv[1]) / "manifest.jsonl").read_text().splitlines()]
v = sorted((r for r in rows if r["doc_id"] == "10CFR50.59"), key=lambda r: r["effective_from"])
fr = {r["doc_id"]: r for r in rows if r["kind"] == "rulemaking"}
for a, b in zip(v, v[1:]):
    rel = "contiguous" if a["effective_to"] == b["effective_from"] else ("GAP" if a["effective_to"] < b["effective_from"] else "OVERLAP")
    print(f"{a['revision']} [{a['effective_from']} , {a['effective_to']})  -> next {b['effective_from']}: {rel}")
print(f"{v[-1]['revision']} [{v[-1]['effective_from']} , open)  status={v[-1]['status']}")
print("earliest coverage starts", v[0]["effective_from"], "-> as_of 1996-04-01 covered:", v[0]["effective_from"] <= "1996-04-01")
eff = sorted(r["effective_from"] for r in fr.values() if r["effective_from"])
print("FR rule effective dates:", eff)
print("version boundaries     :", [r["effective_from"] for r in v])
