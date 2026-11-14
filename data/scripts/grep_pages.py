"""usage: python3 -I grep_pages.py PDF REGEX [WIDTH] -> [pN] context for each match (pdftotext per page)."""
import re, subprocess, sys

def pages(pdf):
    n = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", pdf], capture_output=True, text=True).stdout).group(1))
    for i in range(1, n + 1):
        t = subprocess.run(["pdftotext", "-f", str(i), "-l", str(i), pdf, "-"], capture_output=True, text=True).stdout
        t = re.sub(r"-\n(?=[a-z])", "", t)          # rejoin hyphenated words
        yield i, re.sub(r"\s+", " ", t)

if __name__ == "__main__":
    pdf, rx = sys.argv[1], re.compile(sys.argv[2], re.I)
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 250
    for i, t in pages(pdf):
        for m in rx.finditer(t):
            print(f"[p{i}] ...{t[max(0, m.start()-w):m.end()+w]}...")
