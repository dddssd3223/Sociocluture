"""PHASE 1 step 1: repository file inventory (deterministic)."""
import hashlib, json, os, re, sys, unicodedata
import pymupdf as fitz

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "reports", "SOCIOCULTURE", "inventory.json")
SKIP_DIRS = {".git", "data", "scripts"}

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

def classify(name):
    name = unicodedata.normalize("NFC", name)
    m = re.match(r"(\d{4})학년도_(06월|09월|수능)_(사회문화|사탐전체)_(문제|해설|정답표?)\.(pdf|jpg)$", name)
    if m:
        kind = m.group(4)
        if kind == "문제":
            return "A_SOCIOCULTURE_PROBLEM", "problem"
        if kind == "해설":
            return "B_SOCIOCULTURE_ANSWER", "answer_explanation"
        return "B_SOCIOCULTURE_ANSWER", "answer_key"
    if any(k in name for k in ("통합사회", "2028", "교육과정", "28예시")):
        return "C_REFERENCE_2028", "reference"
    if name.lower().endswith((".svg", ".png", ".jpg", ".jpeg")):
        return "D_ASSET", "asset"
    if name.endswith((".py", ".sh", ".js")):
        return "E_CODE", "code"
    if name.endswith((".db", ".json", ".jsonl", ".sqlite")):
        return "F_DB_JSON", "data"
    return "G_OTHER", "other"

def main():
    rows = []
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
        for fn in sorted(fns):
            if fn.startswith("."):
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, ROOT)
            cat, role = classify(fn)
            r = {"filename": unicodedata.normalize("NFC", fn), "path": rel, "extension": os.path.splitext(fn)[1].lower() or None,
                 "size_bytes": os.path.getsize(p), "sha256": sha256(p), "category": cat, "role": role,
                 "page_count": None, "pdf_kind": None}
            try:
                with open(p, "rb") as f:
                    is_pdf = f.read(5) == b"%PDF-"
            except OSError:
                is_pdf = False
            if is_pdf:
                r["actual_format"] = "pdf"
                d = fitz.open(p)
                r["page_count"] = d.page_count
                chars = [len(pg.get_text().strip()) for pg in d]
                txt = sum(1 for c in chars if c > 50)
                r["text_pages"] = txt
                r["pdf_kind"] = "text" if txt == d.page_count else ("scanned" if txt == 0 else "mixed")
            if r["size_bytes"] < 16 and not is_pdf:
                r["note"] = "placeholder/empty file (no usable content)"
            rows.append(r)
    rows.sort(key=lambda r: r["path"])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    from collections import Counter
    print(Counter(r["category"] for r in rows))
    print(Counter((r["category"], r["pdf_kind"]) for r in rows))

if __name__ == "__main__":
    main()
