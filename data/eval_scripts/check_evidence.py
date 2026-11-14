"""Every evidence string must be a verbatim quote in its source file (after normalization).
Levels: exact-ws (whitespace/hyphen/ligature-normalized), loose (alnum-only, case-folded), page check."""
import json, re, subprocess, sys, unicodedata
from pathlib import Path

DATA = Path(sys.argv[1])

def norm(s):
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("­", "")
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s)      # line-end hyphenation
    s = s.replace("“", '"').replace("”", '"').replace("‘‘", '"').replace("’’", '"')
    s = s.replace("‘", "'").replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip()

def loose(s):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", s).lower())

def text_of(path, page=None):
    if path.suffix == ".pdf":
        args = ["pdftotext"] + (["-f", str(page), "-l", str(page)] if page else []) + [str(path), "-"]
        return subprocess.run(args, capture_output=True, text=True).stdout
    t = path.read_text(errors="replace")
    if path.suffix == ".xml":
        t = re.sub(r"<[^>]+>", " ", t)
    return t

for i, line in enumerate((DATA/"labels"/"supersession_evidence.jsonl").read_text().splitlines(), 1):
    r = json.loads(line)
    src = DATA / re.search(r"\[source file: ([^;\]]+)", r["notes"]).group(1)
    if not src.exists():
        print(f"{i:2} FILE MISSING {src}"); continue
    full = text_of(src); ev = r["evidence"]
    a = norm(ev) in norm(full)
    b = loose(ev) in loose(full)
    pg = ""
    if r["source_page"] and src.suffix == ".pdf":
        pt = text_of(src, r["source_page"])
        pg = "page-ok" if loose(ev) in loose(pt) else "PAGE-MISMATCH"
        if pg == "PAGE-MISMATCH":
            # where is it?
            n = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(src)], capture_output=True, text=True).stdout).group(1))
            hits = [p for p in range(1, n+1) if loose(ev[:60]) in loose(text_of(src, p))]
            pg += f" (first-60-chars found on {hits})"
    status = "EXACT" if a else ("LOOSE-ONLY" if b else "NOT FOUND")
    print(f"{i:2} {r['subject_doc']:10} {status:10} {pg:12} {src.name} :: {ev[:70]}")
