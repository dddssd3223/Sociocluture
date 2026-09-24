"""PHASE 1: extract official answer keys for SOCIOCULTURE exams.

Answer sources are only the answer materials present in the repository:
  * OFFICIAL_ANSWER_TABLE  - 정답 / 정답표 files (평가원 answer tables, incl. 사탐전체 tables)
  * COMMENTARY_ANSWER_KEY  - the answer summary printed in 해설 (정답 및 해설) files
Scanned / image answer tables are transcribed visually (NOT solved) in
manual_transcriptions.json and cross-checked against any machine-readable source.
Nothing here derives an answer from the question content.
"""
import os
import re
import sys

import pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p1_common import (ANS_DIR, CIRCLED, ROOT, dump_json, exam_id, load_json, nfc, parse_fname,
                       repo_files, sha256)

MANUAL = os.path.join(ANS_DIR, "manual_transcriptions.json")
OUT = os.path.join(ANS_DIR, "answer_keys.json")

NUM_RE = re.compile(r"^0?(\d{1,2})[.)]?$")


def words_split(page):
    """Words with circled digits split off into their own tokens."""
    out = []
    for x0, y0, x1, y1, t, *_ in page.get_text("words"):
        t = nfc(t).replace("　", "")
        if not t:
            continue
        # split tokens such as '01.④' or '④2.'
        parts = re.findall(r"[①②③④⑤]|[^①②③④⑤]+", t)
        if len(parts) == 1:
            out.append((x0, y0, x1, y1, t))
            continue
        w = (x1 - x0) / max(len(t), 1)
        pos = x0
        for p in parts:
            out.append((pos, y0, pos + w * len(p), y1, p))
            pos += w * len(p)
    return out


def pair_reading_order(page):
    """Number token followed (in reading order, possibly across a line wrap) by a circled digit."""
    ws = sorted(words_split(page), key=lambda w: (round(w[1] / 4), w[0]))
    got = {}
    for i, w in enumerate(ws[:-1]):
        m = re.match(r"^0?(\d{1,2})\.$", w[4])
        if m and ws[i + 1][4] in CIRCLED and 1 <= int(m.group(1)) <= 20:
            got.setdefault(int(m.group(1)), CIRCLED.index(ws[i + 1][4]) + 1)
    return got


def pair_vertical(page):
    """Grid where the circled digit sits directly below its question number."""
    ws = words_split(page)
    nums = [w for w in ws if re.match(r"^0?(\d{1,2})\.$", w[4])]
    circ = [w for w in ws if w[4] in CIRCLED]
    cands = []
    for ni, n in enumerate(nums):
        q = int(re.match(r"^0?(\d{1,2})\.$", n[4]).group(1))
        for ci, c in enumerate(circ):
            dy, dx = c[1] - n[1], c[0] - n[0]
            if 0 < dy <= 20 and -5 <= dx <= 30 and 1 <= q <= 20:
                cands.append((dy + abs(dx) / 10, ni, ci, q))
    got, un, uc = {}, set(), set()
    for _, ni, ci, q in sorted(cands):
        if ni in un or ci in uc or q in got:
            continue
        un.add(ni); uc.add(ci)
        got[q] = CIRCLED.index(circ[ci][4]) + 1
    return got


def best_pairing(page):
    for name, fn in (("pdf_text_positional", pair_positional), ("pdf_text_reading_order", pair_reading_order),
                     ("pdf_text_vertical_grid", pair_vertical)):
        got = fn(page)
        if sorted(got) == list(range(1, 21)):
            return name, got
    return "pdf_text_positional", pair_positional(page)


def pair_positional(page, max_gap=70):
    """Pair each question-number token with the circled digit right of it on the same line."""
    ws = words_split(page)
    nums = [w for w in ws if NUM_RE.match(w[4]) and 1 <= int(NUM_RE.match(w[4]).group(1)) <= 20]
    circ = [w for w in ws if w[4] in CIRCLED]
    cands = []
    for ni, n in enumerate(nums):
        q = int(NUM_RE.match(n[4]).group(1))
        ny = (n[1] + n[3]) / 2
        h = max(n[3] - n[1], 4)
        for ci, c in enumerate(circ):
            cy = (c[1] + c[3]) / 2
            gap = c[0] - n[2]
            if abs(cy - ny) <= 0.6 * h + 2 and -2 <= gap <= max_gap:
                cands.append((gap, round(n[1]), ni, ci, q))
    got, used_n, used_c = {}, set(), set()
    for gap, _, ni, ci, q in sorted(cands):
        if ni in used_n or ci in used_c or q in got:
            continue
        used_n.add(ni)
        used_c.add(ci)
        got[q] = CIRCLED.index(circ[ci][4]) + 1
    return got


def parse_row_table(page):
    """사탐전체-style table: a row labelled 사회·문화 followed by 20 circled digits."""
    ws = words_split(page)
    for w in ws:
        t = w[4]
        if "사회" in t and "문화" in t or t in ("사회․문화", "사회·문화"):
            y = (w[1] + w[3]) / 2
            row = sorted((c for c in ws if c[4] in CIRCLED and abs((c[1] + c[3]) / 2 - y) < 4), key=lambda c: c[0])
            if len(row) == 20:
                return {i + 1: CIRCLED.index(c[4]) + 1 for i, c in enumerate(row)}
    return {}


def per_question_markers(doc):
    """'정답 ④' markers inside commentary bodies, in reading order (used only as a cross-check)."""
    t = nfc("".join(p.get_text() for p in doc))
    return [CIRCLED.index(c) + 1 for c in re.findall(r"정\s*답\s*([①②③④⑤])", t)]


def main():
    files = repo_files()
    manual = load_json(MANUAL, {})
    exams = {}
    for name in sorted(files):
        meta = parse_fname(name)
        if not meta or meta["kind"] == "문제":
            continue
        path = os.path.join(ROOT, files[name])
        eid = exam_id(meta["year"], meta["exam_type"])
        kind = "COMMENTARY_ANSWER_KEY" if meta["kind"] == "해설" else "OFFICIAL_ANSWER_TABLE"
        src = {"source_file": name, "sha256": sha256(path), "source_kind": kind, "answers": {},
               "method": None, "notes": []}
        if name in manual:
            m = manual[name]
            if m["sha256"] != src["sha256"]:
                src["notes"].append("MANUAL_TRANSCRIPTION_SHA_MISMATCH")
            else:
                src["answers"] = {int(k): v for k, v in m["answers"].items()}
                src["method"] = "manual_visual_transcription"
                if m.get("points"):
                    src["points"] = {int(k): v for k, v in m["points"].items()}
        if not src["answers"] and meta["ext"] == "pdf":
            doc = pymupdf.open(path)
            page = doc[0]
            got = parse_row_table(page)
            if got:
                src["answers"], src["method"] = got, "pdf_text_row_table"
            if not src["answers"]:
                meth, got = best_pairing(page)
                if got:
                    src["answers"], src["method"] = got, meth
            if kind == "COMMENTARY_ANSWER_KEY":
                markers = per_question_markers(doc)
                src["per_question_markers"] = markers
        # machine cross-check of a manual transcription when the PDF also has a text layer
        if src["method"] == "manual_visual_transcription" and meta["ext"] == "pdf":
            doc = pymupdf.open(path)
            seq = [CIRCLED.index(c) + 1 for c in re.findall(r"[①②③④⑤]", nfc(doc[0].get_text()))]
            if len(seq) == 20:
                # tables are 5 rows x 4 blocks, text order is row-major: q = row + 5*block
                rowmajor = {r + 1 + 5 * b: seq[r * 4 + b] for r in range(5) for b in range(4)}
                src["text_layer_crosscheck"] = "MATCH" if rowmajor == src["answers"] else "MISMATCH"
        exams.setdefault(eid, {"exam_id": eid, "year": meta["year"], "exam_type": meta["exam_type"],
                               "sources": []})["sources"].append(src)

    # ---- per exam resolution
    for eid, ex in sorted(exams.items()):
        issues = []
        for s in ex["sources"]:
            a = s["answers"]
            ok = sorted(a) == list(range(1, 21)) and all(v in (1, 2, 3, 4, 5) for v in a.values())
            s["complete"] = ok
            if not ok:
                issues.append(f"INCOMPLETE_SOURCE:{s['source_file']}:{len(a)}")
            mk = s.get("per_question_markers")
            if mk and len(mk) == 20 and ok:
                s["marker_crosscheck"] = "MATCH" if mk == [a[i] for i in range(1, 21)] else "MISMATCH"
                if s["marker_crosscheck"] == "MISMATCH":
                    diffs = [i for i in range(1, 21) if mk[i - 1] != a[i]]
                    s["marker_mismatch_questions"] = diffs
            if s.get("text_layer_crosscheck") == "MISMATCH":
                issues.append(f"TEXT_LAYER_MISMATCH:{s['source_file']}")
        good = [s for s in ex["sources"] if s["complete"]]
        official = [s for s in good if s["source_kind"] == "OFFICIAL_ANSWER_TABLE"]
        primary = (official or good or [None])[0]
        final = {}
        per_q_flags = {}
        if primary:
            for q in range(1, 21):
                vals = {s["source_file"]: s["answers"][q] for s in good}
                if len(set(vals.values())) == 1:
                    final[q] = primary["answers"][q]
                else:
                    final[q] = None
                    per_q_flags[q] = f"SOURCE_DISAGREEMENT:{vals}"
                    issues.append(f"Q{q}_SOURCE_DISAGREEMENT")
                s_m = primary.get("marker_mismatch_questions") or []
                if q in s_m:
                    final[q] = None
                    per_q_flags[q] = "COMMENTARY_SUMMARY_VS_BODY_MISMATCH"
                    issues.append(f"Q{q}_COMMENTARY_MISMATCH")
        ex["primary_source"] = primary["source_file"] if primary else None
        ex["primary_source_kind"] = primary["source_kind"] if primary else None
        ex["answers"] = {str(k): v for k, v in final.items()}
        ex["points"] = {str(k): v for k, v in (primary or {}).get("points", {}).items()}
        ex["per_question_flags"] = {str(k): v for k, v in per_q_flags.items()}
        ex["n_sources_agreeing"] = len(good)
        ex["issues"] = issues
        for s in ex["sources"]:
            s["answers"] = {str(k): v for k, v in sorted(s["answers"].items())}
    dump_json(OUT, {"subject": "SOCIOCULTURE", "exams": [exams[k] for k in sorted(exams)]})
    for eid in sorted(exams):
        ex = exams[eid]
        a = ex["answers"]
        print(eid, ex["primary_source_kind"], "".join(str(a.get(str(i)) or "_") for i in range(1, 21)),
              [(s["source_file"][:14], s["method"], s.get("marker_crosscheck"), s.get("text_layer_crosscheck"))
               for s in ex["sources"]], ex["issues"])


if __name__ == "__main__":
    main()
