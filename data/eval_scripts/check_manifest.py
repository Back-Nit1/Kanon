"""Manifest integrity: fields, enums, ISO dates, file existence, sha256, page counts, text layer."""
import hashlib, json, re, subprocess, sys
from datetime import date
from pathlib import Path

DATA = Path(sys.argv[1])
FIELDS = ["doc_id","revision","family","kind","title","effective_from","effective_to","issue_date",
          "status","accession","source_url","local_path","sha256","paginated","page_count",
          "fetched_at","verified","date_basis","notes"]
KINDS = {"regulation","reg_guide","tech_spec","license_amendment","operating_experience","rulemaking","other"}
STATUS = {"active","superseded","withdrawn"}
keys = set()
for i, line in enumerate((DATA/"manifest.jsonl").read_text().splitlines(), 1):
    r = json.loads(line); probs = []
    missing = [f for f in FIELDS if f not in r]; extra = [k for k in r if k not in FIELDS]
    if missing: probs.append(f"missing {missing}")
    if extra: probs.append(f"extra {extra}")
    if r["kind"] not in KINDS: probs.append(f"kind {r['kind']}")
    if r["status"] not in STATUS: probs.append(f"status {r['status']}")
    for f in ("effective_from","effective_to","issue_date"):
        if r[f] is not None:
            try: date.fromisoformat(r[f]); assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", r[f])
            except Exception: probs.append(f"{f} not ISO: {r[f]}")
    if r["effective_from"] and r["effective_to"] and r["effective_to"] <= r["effective_from"]:
        probs.append("effective_to <= effective_from")
    k = (r["doc_id"], r["revision"])
    if k in keys: probs.append("duplicate key")
    keys.add(k)
    p = DATA / r["local_path"]
    if not p.exists():
        probs.append("FILE MISSING")
    else:
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        if h != r["sha256"]: probs.append(f"sha mismatch {h}")
        if p.suffix == ".pdf":
            info = subprocess.run(["pdfinfo", str(p)], capture_output=True, text=True).stdout
            pages = int(re.search(r"Pages:\s+(\d+)", info).group(1))
            if pages != r["page_count"]: probs.append(f"page_count {r['page_count']} != {pages}")
            if not r["paginated"]: probs.append("pdf but paginated=false")
            # text layer: chars per page
            empty = []
            for pg in range(1, pages+1):
                t = subprocess.run(["pdftotext","-f",str(pg),"-l",str(pg),str(p),"-"],capture_output=True,text=True).stdout
                if len(t.strip()) < 200: empty.append(pg)
            tl = f"text-layer ok ({pages} pp)" if not empty else f"LOW-TEXT pages {empty}"
        else:
            tl = "non-pdf"
            if r["paginated"]: probs.append("non-pdf but paginated=true")
            if r["page_count"] is not None: probs.append("non-pdf page_count set")
    if r["status"] == "active" and r["effective_to"]: probs.append("active but effective_to set")
    if r["status"] == "superseded" and not r["effective_to"]: probs.append("superseded but no effective_to")
    print(f"{i:2} {r['doc_id']:14} {r['revision']:11} {'OK' if not probs else 'PROBLEMS: '+'; '.join(probs)} | {tl}")
