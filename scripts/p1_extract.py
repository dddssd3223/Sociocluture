"""PHASE 1: locate questions in SOCIOCULTURE problem PDFs, crop PNGs, extract text.

Usage:
  python3 scripts/p1_extract.py                      # all problem PDFs (skips up-to-date exams)
  python3 scripts/p1_extract.py --only 2020_JUNE ... # subset (smoke test)
  python3 scripts/p1_extract.py --force              # ignore checkpoints

Layout model: 2-column pages separated by a vertical divider rule.  The exam is read as a
stream of column segments (page, column, y-range); a question runs from its number anchor to
the next anchor, and may continue across columns/pages (the PNG is stitched vertically).
Anchors come from the PDF text layer when complete, otherwise from a structural scan of the
rendered page (bold number at the column margin) + digit OCR.
"""
import argparse
import hashlib
import io
import os
import re
import subprocess
import sys
import tempfile

import numpy as np
import pymupdf
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p1_common import (CACHE_DIR, CIRCLED, EXAM_PERIOD_LABEL, PIPELINE_VERSION, Q_IMG_DIR, Q_JSON_DIR, ROOT,
                       SUBJECT, dump_json, exam_id, load_json, nfc, parse_fname, question_id, repo_files, sha256)

TARGET_W = 2339          # rendered page width in px (~200 dpi for A4-width layouts)
ANCHOR_RE = re.compile(r"^(\d{1,2})\.")
HANGUL_RE = re.compile(r"[가-힣]")


# ----------------------------------------------------------------------------- rendering
def render_gray(page, zoom):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.stride)[:, :pix.w].copy()


def longest_vertical_run(col, maxgap=4):
    ys = np.flatnonzero(col)
    if len(ys) == 0:
        return (0, 0, 0)
    brk = np.flatnonzero(np.diff(ys) > maxgap)
    starts = np.r_[ys[0], ys[brk + 1]]
    ends = np.r_[ys[brk], ys[-1]]
    k = int(np.argmax(ends - starts))
    return (int(ends[k] - starts[k]), int(starts[k]), int(ends[k]))


def find_divider(gray):
    """Vertical column rule near the page centre -> (x, y_top, y_bottom) in px, or None."""
    H, W = gray.shape
    best = (0, 0, 0, 0)
    for cx in range(int(W * 0.42), int(W * 0.58)):
        r = longest_vertical_run(gray[:, cx] < 150, maxgap=int(4 * W / 842) + 1)
        if r[0] > best[0]:
            best = r + (cx,)
    if best[0] < 0.45 * H:
        return None
    return {"x": best[3], "top": best[1], "bottom": best[2]}


# ----------------------------------------------------------------------------- OCR
def tesseract(img, args):
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img.save(f.name)
        name = f.name
    try:
        r = subprocess.run(["tesseract", name, "-"] + args, capture_output=True, text=True)
        return r.stdout
    finally:
        os.unlink(name)


def ocr_digits(arr):
    img = Image.fromarray(arr)
    return tesseract(img, ["--psm", "7", "-c", "tessedit_char_whitelist=0123456789."]).strip()


def ocr_text(img):
    return tesseract(img, ["-l", "kor+eng", "--psm", "4"]).strip()


# ----------------------------------------------------------------------------- anchors
def text_anchor_candidates(doc):
    """(page_idx, col, y_pt, x_pt, number) candidates from the PDF text layer."""
    cands = []
    for pi, page in enumerate(doc):
        W = page.rect.width
        for w in page.get_text("words"):
            m = ANCHOR_RE.match(w[4])
            if not m:
                continue
            x0 = w[0]
            if x0 < 0.2 * W:
                col = 0
            elif 0.45 * W < x0 < 0.62 * W:
                col = 1
            else:
                continue
            cands.append({"page": pi, "col": col, "y": w[1], "y1": w[3], "x": x0, "num": int(m.group(1))})
    return cands


def modal_x(xs, tol):
    best, bx = 0, None
    for x in xs:
        c = sum(1 for y in xs if abs(y - x) <= tol)
        if c > best or (c == best and bx is not None and x < bx):
            best, bx = c, x
    return bx


def filter_margin(cands, tol=6):
    out = []
    for col in (0, 1):
        cc = [c for c in cands if c["col"] == col]
        if not cc:
            continue
        mx = modal_x([round(c["x"]) for c in cc], tol)
        out += [c for c in cc if abs(c["x"] - mx) <= tol]
    return out


def sequence(cands, n_max=20):
    """Greedy in reading order: accept 1,2,3,... ; returns accepted anchors."""
    cands = sorted(cands, key=lambda c: (c["page"], c["col"], c["y"]))
    acc, exp = [], 1
    for c in cands:
        if c["num"] == exp:
            acc.append(c)
            exp += 1
            if exp > n_max:
                break
    return acc


def line_starts(gray, div):
    """Text-line blobs near each column's left margin: (col, y0_px, y1_px, x_px, x_region0)."""
    H, W = gray.shape
    ink = gray < 140
    s = W / 2339.0
    top = max(0, div["top"] - int(40 * s)) if div else int(0.08 * H)
    bot = div["bottom"] if div else int(0.93 * H)
    dx = div["x"] if div else W // 2
    out = []
    for col, (x0, x1) in enumerate([(int(0.03 * W), dx - int(8 * s)), (dx + int(8 * s), int(0.97 * W))]):
        strip_w = int(0.3 * (x1 - x0))
        reg = ink[top:bot, x0:x0 + strip_w]
        ys = np.flatnonzero(reg.any(1))
        if len(ys) == 0:
            continue
        brk = np.flatnonzero(np.diff(ys) > max(2, int(3 * s)))
        for y0, y1 in zip(np.r_[ys[0], ys[brk + 1]], np.r_[ys[brk], ys[-1]]):
            if int(18 * s) <= y1 - y0 <= int(50 * s):
                sx = int(np.flatnonzero(reg[y0:y1 + 1].any(0))[0])
                out.append((col, top + int(y0), top + int(y1), x0 + sx))
    return out


def margin_x(all_starts, col, s):
    xs = sorted(st[3] for st in all_starts if st[0] == col)
    for v in xs:
        if sum(1 for x in xs if abs(x - v) <= 6 * s) >= 6:
            return v
    return None


def structural_candidates(gray, zoom, pi, starts, colx):
    """Image-based anchor candidates for one page (numbers bold at the column margin)."""
    H, W = gray.shape
    ink = gray < 140
    s = W / 2339.0
    out = []
    for col, yy, yend, sx in starts:
        if colx.get(col) is None or abs(sx - colx[col]) > 8 * s:
            continue
        h = yend - yy
        band = ink[yy:yend + 1, sx:sx + int(160 * s)].any(0)
        e, gap = 0, 0
        for i, v in enumerate(band):
            if v:
                e, gap = i, 0
            else:
                gap += 1
                if gap >= int(9 * s) and e > 0:
                    break
        if not (int(12 * s) <= e <= int(75 * s)):
            continue
        after = ink[yy:yend + 1, sx + e + int(5 * s):sx + e + int(200 * s)]
        if not after.any():
            continue
        pad = int(8 * s)
        crop = gray[max(0, yy - pad):yend + pad, max(0, sx - pad):sx + e + pad]
        t = ocr_digits(crop)
        m = re.fullmatch(r"(\d{1,2})\.", t)
        out.append({"page": pi, "col": col, "y": yy / zoom, "y1": yend / zoom,
                    "x": sx / zoom, "num": int(m.group(1)) if m else None, "ocr": t})
    return out


def resolve_structural(cands, n_max=20):
    """Use OCR-read numbers that form an increasing chain; fill gaps positionally when counts fit."""
    cands = sorted(cands, key=lambda c: (c["page"], c["col"], c["y"]))
    rel = [(i, c["num"]) for i, c in enumerate(cands) if c["num"] and 1 <= c["num"] <= n_max]
    # longest strictly increasing subsequence (by number) where number gaps fit the index gaps
    best = {}
    for k, (i, n) in enumerate(rel):
        best[k] = (1, None)
        for j in range(k):
            ij, nj = rel[j]
            if nj < n and best[j][0] + 1 > best[k][0]:
                best[k] = (best[j][0] + 1, j)
    if not best:
        return [], []
    k = max(best, key=lambda z: best[z][0])
    chain = []
    while k is not None:
        chain.append(rel[k])
        k = best[k][1]
    chain = chain[::-1]
    fixed = {i: n for i, n in chain}
    # sentinels
    pts = [(-1, 0)] + chain + [(len(cands), n_max + 1)]
    inferred = []
    for (ia, na), (ib, nb) in zip(pts, pts[1:]):
        between = list(range(ia + 1, ib))
        missing = nb - na - 1
        if missing <= 0:
            continue
        if len(between) == missing:
            for off, idx in enumerate(between):
                fixed[idx] = na + 1 + off
                inferred.append(na + 1 + off)
        elif ib == len(cands) and len(between) >= missing:
            continue
    acc = []
    for idx in sorted(fixed):
        c = dict(cands[idx])
        c["num"] = fixed[idx]
        c["inferred"] = fixed[idx] in inferred
        acc.append(c)
    acc.sort(key=lambda c: c["num"])
    return acc, inferred


GROUP_RE = re.compile(r"^\s*[\[ġ]\s*(\d{1,2})\s*[~～∼\-–]\s*(\d{1,2})\s*[\]Ģ]")  # ġ/Ģ: bracket glyphs in some fonts


def text_group_headers(doc):
    """Shared-passage instructions such as '[10～11] 다음 자료를 읽고 물음에 답하시오.'"""
    out = []
    for pi, page in enumerate(doc):
        W = page.rect.width
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                t = nfc("".join(sp["text"] for sp in l["spans"]))
                m = GROUP_RE.match(t)
                if not m:
                    continue
                x0 = l["bbox"][0]
                col = 0 if x0 < 0.3 * W else 1
                out.append({"page": pi, "col": col, "y": l["bbox"][1], "x": x0, "first": int(m.group(1)),
                            "last": int(m.group(2)), "text": t.strip()})
    return out


# ----------------------------------------------------------------------------- helpers
def page_text_quality(page):
    t = page.get_text()
    nonspace = [ch for ch in t if not ch.isspace()]
    if len(nonspace) < 200:
        return 0.0
    return len(HANGUL_RE.findall(t)) / len(nonspace)


def header_metadata(doc, first_page, zoom_gray=None):
    """Read year / exam kind from the printed header of the exam's first pages."""
    txt = ""
    for pi in range(0, min(first_page + 1, doc.page_count)):
        txt += nfc(doc[pi].get_text())[:600]
    src = "pdf_text"
    if not re.search(r"\d{4}\s*학년도", txt):
        page = doc[first_page]
        z = TARGET_W / page.rect.width
        g = render_gray(page, z)
        txt = ocr_text(Image.fromarray(g[: int(0.2 * g.shape[0])]))
        src = "ocr"
    t = re.sub(r"\s+", "", txt)
    m = re.search(r"(20\d{2})[가-힣]?년도", t)
    year = int(m.group(1)) if m else None
    if re.search(r"6월모의(평가|고사)", t):
        et = "JUNE"
    elif re.search(r"9월모의(평가|고사)", t):
        et = "SEPTEMBER"
    elif re.search(r"능력시험", t) and not re.search(r"모의", t):
        et = "CSAT"
    else:
        et = None
    subj_ok = bool(re.search(r"사회.{0,2}문화", t))
    return {"year": year, "exam_type": et, "subject_in_header": subj_ok, "source": src, "raw": txt[:200]}


def trim_ink(gray, x0, x1, y0, y1, thr=170, min_px=6):
    """Trim blank rows at top/bottom of a region; None if the region is blank."""
    reg = gray[y0:y1, x0:x1] < thr
    rows = np.flatnonzero(reg.sum(1) >= 1)
    if len(rows) == 0 or reg.sum() < min_px * 10 or rows[-1] - rows[0] < 12:
        return None
    return (y0 + int(rows[0]), y0 + int(rows[-1]) + 1)


def split_choices(text):
    """Return (stem, passage, choices, ok)."""
    t = text
    pos = []
    start = 0
    # choose the last ① after which ②③④⑤ follow in order
    idx1 = [m.start() for m in re.finditer("①", t)]
    chosen = None
    for i1 in reversed(idx1):
        p, ok, cur = [i1], True, i1
        for c in CIRCLED[1:]:
            j = t.find(c, cur + 1)
            if j < 0:
                ok = False
                break
            p.append(j)
            cur = j
        if ok:
            chosen = p
            break
    body = t
    choices = []
    if chosen:
        body = t[:chosen[0]]
        for k in range(5):
            seg = t[chosen[k] + 1: chosen[k + 1] if k < 4 else len(t)]
            choices.append({"number": k + 1, "text": re.sub(r"\s+", " ", seg).strip()})
    m = re.match(r"\s*\d{1,2}\s*\.\s*(.*?\?)", body, re.S)
    if m and len(m.group(1)) < 400:
        end = m.end()
        rest = body[end:]
        cond = re.match(r"\s*\(\s*단", rest)
        if cond:                      # include a trailing "(단, ...)" condition, balancing parentheses
            depth, j = 0, 0
            for j, ch in enumerate(rest):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
            if depth == 0 and j < 400:
                end += j + 1
        pts = re.match(r"\s*[\[~ġ]\s*3\s*점\s*[\]₩Ģ]?", body[end:])
        if pts:
            end += pts.end()
        stem = re.sub(r"\s+", " ", body[:end]).strip()
        passage = body[end:].strip()
    else:
        stem, passage = "", body.strip()
    ok = bool(chosen) and all(c["text"] for c in choices) and all(len(c["text"]) < 300 for c in choices)
    return stem, passage, choices, ok


def visual_flag_textpage(page, rect):
    """Visual-material signals inside one question segment of a text-layer page."""
    n_img = n_tab = n_diag = n_curve = n_fill = 0
    for info in page.get_image_info():
        b = pymupdf.Rect(info["bbox"])
        if b.width < 25 or b.height < 20 or b.width > 0.8 * page.rect.width:
            continue
        if (b & rect).get_area() > 0.5 * b.get_area():
            n_img += 1
    try:
        for t in page.find_tables(clip=rect).tables:
            if t.row_count >= 2 and t.col_count >= 2:
                n_tab += 1
    except Exception:
        pass
    for d in page.get_drawings():
        if not rect.contains(d["rect"]):
            continue
        f = d.get("fill")
        if f is not None and min(f) < 0.97 and d["rect"].get_area() < 0.3 * rect.get_area() \
                and d["rect"].width > 3 and d["rect"].height > 3:
            n_fill += 1
        for it in d["items"]:
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                if abs(p1.y - p2.y) >= 0.5 and abs(p1.x - p2.x) >= 0.5:
                    n_diag += 1
            elif it[0] == "c":
                n_curve += 1
    return {"images": n_img, "tables": n_tab, "diagonal_lines": n_diag, "curves": n_curve, "filled_shapes": n_fill}


# ----------------------------------------------------------------------------- main per exam
def process_exam(src_name, src_path, meta, force=False):
    eid = exam_id(meta["year"], meta["exam_type"])
    src_sha = sha256(src_path)
    out_json = os.path.join(Q_JSON_DIR, f"{eid}.json")
    prev = load_json(out_json)
    if (not force and prev and prev.get("source_sha256") == src_sha and prev.get("pipeline_version") == PIPELINE_VERSION
            and all(os.path.exists(os.path.join(ROOT, q["question_image"])) for q in prev["questions"] if q["question_image"])):
        print(f"[skip] {eid} up to date")
        return prev

    doc = pymupdf.open(src_path)
    cache_dir = os.path.join(CACHE_DIR, src_sha[:16])
    os.makedirs(cache_dir, exist_ok=True)

    # ---- page layout (dividers)
    layout = []
    grays = {}
    for pi, page in enumerate(doc):
        z = TARGET_W / page.rect.width
        g = render_gray(page, z)
        grays[pi] = (g, z)
        div = find_divider(g)
        layout.append({"page": pi, "zoom": z, "w_pt": page.rect.width, "h_pt": page.rect.height,
                       "divider": {k: v / z for k, v in div.items()} if div else None,
                       "text_quality": round(page_text_quality(page), 3)})
    good = [l for l in layout if l["divider"]]
    widths = sorted(set(round(l["w_pt"]) for l in layout))
    main_w = max(widths, key=lambda w: sum(1 for l in layout if round(l["w_pt"]) == w))

    # ---- anchors
    review_exam = []
    tc = filter_margin(text_anchor_candidates(doc))
    anchors = sequence(tc)
    anchor_method = "pdf_text"
    inferred = []
    if len(anchors) < 20:
        sc, starts = [], {}
        for pi, page in enumerate(doc):
            if round(page.rect.width) != main_w:
                continue
            g, z = grays[pi]
            d = layout[pi]["divider"]
            dpx = {k: int(v * z) for k, v in d.items()} if d else None
            starts[pi] = line_starts(g, dpx)
        pooled = [st for v in starts.values() for st in v]
        sfac = TARGET_W / 2339.0
        colx = {c: margin_x(pooled, c, sfac) for c in (0, 1)}
        for pi in starts:
            g, z = grays[pi]
            sc += structural_candidates(g, z, pi, starts[pi], colx)
        s_anchors, inferred = resolve_structural(sc)
        if len(s_anchors) > len(anchors):
            anchors, anchor_method = s_anchors, "image_structural_ocr"
        dump_json(os.path.join(cache_dir, "structural_candidates.json"), sc)
    found = sorted(a["num"] for a in anchors)
    missing = [n for n in range(1, 21) if n not in found]

    if not anchors:
        raise RuntimeError(f"{eid}: no anchors found")
    first_page = anchors[0]["page"]
    hdr = header_metadata(doc, first_page)

    # ---- column geometry
    def col_bounds(pi):
        l = layout[pi]
        W, H = l["w_pt"], l["h_pt"]
        d = l["divider"]
        if d is None:
            same = [g for g in good if g["page"] != first_page and round(g["w_pt"]) == round(W)] or \
                   [g for g in good if round(g["w_pt"]) == round(W)]
            if same:
                d = {"x": float(np.median([g["divider"]["x"] for g in same])),
                     "top": float(np.median([g["divider"]["top"] for g in same])),
                     "bottom": float(np.median([g["divider"]["bottom"] for g in same]))}
            else:
                d = {"x": W / 2, "top": 0.10 * H, "bottom": 0.92 * H}
        lx = [a["x"] for a in anchors if a["col"] == 0 and round(layout[a["page"]]["w_pt"]) == round(W)]
        margin = (float(np.median(lx)) if lx else 0.08 * W) - 14
        margin = max(margin, 2)
        return {0: (margin, d["x"] - 2), 1: (d["x"] + 2, min(W - 2, W - margin + 6))}, d

    # ---- stream of column segments
    stream = []
    for pi in range(first_page, doc.page_count):
        if round(layout[pi]["w_pt"]) != main_w:
            continue
        cols, d = col_bounds(pi)
        for c in (0, 1):
            top = d["top"] + 4
            if pi == first_page:
                # page-wide top: content above the first anchor on page 1 is exam instructions
                top = min(a["y"] for a in anchors if a["page"] == pi) - 8
            stream.append({"page": pi, "col": c, "x0": cols[c][0], "x1": cols[c][1], "top": top,
                           "bottom": d["bottom"] - 1})

    def seg_index(a):
        for i, s in enumerate(stream):
            if s["page"] == a["page"] and s["col"] == a["col"]:
                return i
        return None

    anchors = sorted(anchors, key=lambda a: a["num"])
    by_num = {a["num"]: a for a in anchors}
    # shared-passage group headers ([N~M] ...) from the text layer or manual overrides
    groups = []
    if anchor_method == "pdf_text":
        groups = [g for g in text_group_headers(doc) if g["first"] in by_num]
    manual_groups = (load_json(os.path.join(Q_JSON_DIR, "..", "manual_group_headers.json"), {}) or {}).get(eid, [])
    for g in manual_groups:
        groups.append({"page": g["page"] - 1, "col": g["col"], "y": g["y_pt"], "x": None, "first": g["first"],
                       "last": g["last"], "text": g.get("note", "manual"), "manual": True})
    boundaries = sorted([(seg_index(a), a["y"]) for a in anchors] + [(seg_index(g), g["y"]) for g in groups])

    def region(item):
        si, ys = seg_index(item), item["y"]
        nb = next(((k, y) for k, y in boundaries if (k, y) > (si, ys + 1)), None)
        ei = nb[0] if nb else len(stream) - 1
        out = []
        for k in range(si, ei + 1):
            st = stream[k]
            y0 = ys - 7 if k == si else st["top"]
            y1 = (nb[1] - 5) if (nb and k == ei) else st["bottom"]
            if y1 - y0 < 4:
                continue
            out.append({"page": st["page"], "col": st["col"], "x0": st["x0"], "x1": st["x1"], "y0": y0, "y1": y1})
        return out

    questions = []
    os.makedirs(Q_IMG_DIR, exist_ok=True)
    for a in anchors:
        q = a["num"]
        nxt = next((by_num[n2] for n2 in range(q + 1, 21) if n2 in by_num), None)
        gap_to_missing = nxt is not None and nxt["num"] != q + 1
        grp = next((g for g in groups if g["first"] <= q <= g["last"]), None)
        parts = ([dict(p_, shared=True) for p_ in region(grp)] if grp else []) + region(a)
        # render / trim / stitch
        imgs, used = [], []
        for p in parts:
            g, z = grays[p["page"]]
            X0, X1 = int(p["x0"] * z), int(p["x1"] * z)
            Y0, Y1 = int(max(0, p["y0"]) * z), int(p["y1"] * z)
            tr = trim_ink(g, X0, X1, Y0, Y1)
            if tr is None:
                continue
            Y0 = max(Y0, tr[0] - 6)
            Y1 = min(Y1, tr[1] + 6)
            imgs.append(g[Y0:Y1, X0:X1])
            used.append(dict(p, y0=round(Y0 / z, 2), y1=round(Y1 / z, 2), x0=round(p["x0"], 2), x1=round(p["x1"], 2)))
        Wmax = max(im.shape[1] for im in imgs)
        sep = 14
        Htot = sum(im.shape[0] for im in imgs) + sep * (len(imgs) - 1)
        canvas = np.full((Htot, Wmax), 255, np.uint8)
        y = 0
        for i, im in enumerate(imgs):
            canvas[y:y + im.shape[0], :im.shape[1]] = im
            y += im.shape[0]
            if i < len(imgs) - 1:
                canvas[y + sep // 2, ::6] = 170   # dotted rule marks a column/page join
                y += sep
        qid = question_id(meta["year"], meta["exam_type"], q)
        rel_img = os.path.relpath(os.path.join(Q_IMG_DIR, f"{qid}.png"), ROOT)
        pil = Image.fromarray(canvas)
        buf = io.BytesIO()
        pil.save(buf, format="PNG", optimize=True)
        with open(os.path.join(ROOT, rel_img), "wb") as f:
            f.write(buf.getvalue())
        img_sha = hashlib.sha256(buf.getvalue()).hexdigest()

        # ---- text
        text_parts, low_q = [], False
        for p in used:
            page = doc[p["page"]]
            if layout[p["page"]]["text_quality"] < 0.3:
                low_q = True
                break
            r = pymupdf.Rect(p["x0"], p["y0"], p["x1"], p["y1"])
            text_parts.append((bool(p.get("shared")), nfc(page.get_text("text", clip=r, sort=True))))
        if not low_q and len(HANGUL_RE.findall(" ".join(t_ for sh, t_ in text_parts if not sh))) < 40:
            low_q = True      # question itself is a raster image on a page that has some text layer
        ocr_cache = os.path.join(cache_dir, f"ocr_Q{q:02d}_{img_sha[:12]}.txt")
        if low_q:
            if os.path.exists(ocr_cache):
                text = open(ocr_cache, encoding="utf-8").read()
            else:
                text = nfc(ocr_text(pil))
                with open(ocr_cache, "w", encoding="utf-8") as f:
                    f.write(text)
            text_source = "tesseract_ocr"
            shared_text = ""
            if grp:
                mm = re.search(r"(?m)^\s*%d\s*[.,]" % q, text)
                if mm:
                    shared_text, text = text[:mm.start()].strip(), text[mm.start():]
        else:
            text = "\n".join(t_ for sh, t_ in text_parts if not sh).strip()
            shared_text = "\n".join(t_ for sh, t_ in text_parts if sh).strip()
            text_source = "pdf_text_layer"
        # text inside embedded raster images is absent from the text layer -> OCR those regions
        img_ocr = []
        if not low_q:
            for p in used:
                page = doc[p["page"]]
                r = pymupdf.Rect(p["x0"], p["y0"], p["x1"], p["y1"])
                g, z = grays[p["page"]]
                for info in page.get_image_info():
                    b = pymupdf.Rect(info["bbox"]) & r
                    if b.width < 60 or b.height < 30 or b.width > 0.8 * page.rect.width:
                        continue
                    crop = g[int(b.y0 * z):int(b.y1 * z), int(b.x0 * z):int(b.x1 * z)]
                    key = hashlib.sha256(crop.tobytes()).hexdigest()[:16]
                    cp = os.path.join(cache_dir, f"imgocr_{key}.txt")
                    if os.path.exists(cp):
                        t_ = open(cp, encoding="utf-8").read()
                    else:
                        t_ = nfc(ocr_text(Image.fromarray(crop)))
                        with open(cp, "w", encoding="utf-8") as f:
                            f.write(t_)
                    if t_.strip():
                        img_ocr.append(t_.strip())
        stem, passage, choices, choices_ok = split_choices(text)
        points = 3 if re.search(r"[\[~ġ]\s*3\s*점\s*[\]₩Ģ]?", text[:600]) else (2 if text_source == "pdf_text_layer" else None)

        # ---- visual flag
        visual, vis_detail = None, None
        if not low_q:
            vis_detail = {"images": 0, "tables": 0, "diagonal_lines": 0, "curves": 0, "filled_shapes": 0}
            for p in used:
                r = pymupdf.Rect(p["x0"], p["y0"], p["x1"], p["y1"])
                for k_, v_ in visual_flag_textpage(doc[p["page"]], r).items():
                    vis_detail[k_] += v_
            visual = bool(vis_detail["images"] or vis_detail["tables"] or vis_detail["diagonal_lines"] >= 3
                          or vis_detail["curves"] >= 16 or vis_detail["filled_shapes"] >= 2)

        rr = []
        if a.get("inferred"):
            rr.append("QUESTION_NUMBER_REVIEW")
        if gap_to_missing:
            rr.append("CROP_REVIEW")          # next question missing: crop may include it
        if not choices_ok:
            rr.append("CHOICE_TEXT_UNRELIABLE")
        hang = len(HANGUL_RE.findall(text + " ".join(img_ocr)))
        if hang < 40:
            rr.append("OCR_LOW_CONFIDENCE")
        if visual is None:
            rr.append("VISUAL_FLAG_REVIEW")
        questions.append({
            "question_id": qid, "subject": SUBJECT, "exam_id": eid,
            "year": meta["year"], "exam_type": meta["exam_type"], "exam_period": EXAM_PERIOD_LABEL[meta["exam_type"]],
            "question_number": q,
            "source_file": src_name, "source_sha256": src_sha,
            "source_page": used[0]["page"] + 1 if used else None,
            "original_page": used[0]["page"] + 1 if used else None,
            "source_pages": sorted(set(p["page"] + 1 for p in used)),
            "crop_segments": [{"page": p["page"] + 1, "column": "L" if p["col"] == 0 else "R",
                               "shared_passage": bool(p.get("shared")),
                               "bbox_pt": [p["x0"], p["y0"], p["x1"], p["y1"]]} for p in used],
            "shared_passage_group": ({"range": f"{grp['first']}-{grp['last']}", "header_text": grp["text"],
                                      "manual": bool(grp.get("manual"))} if grp else None),
            "anchor": {"method": anchor_method, "page": a["page"] + 1, "y_pt": round(a["y"], 2),
                       "inferred": bool(a.get("inferred")), "ocr": a.get("ocr")},
            "question_text": text, "shared_passage_text": shared_text, "stem": stem, "passage": passage, "choices": choices,
            "text_source": text_source, "embedded_image_ocr_text": "\n\n".join(img_ocr),
            "points_from_text": points,
            "official_answer": None,
            "has_visual_material": visual, "visual_detail": vis_detail,
            "question_image": rel_img, "question_image_sha256": img_sha,
            "image_size_px": [int(canvas.shape[1]), int(canvas.shape[0])],
            "review_required": bool(rr), "review_reasons": rr,
        })
        print(f"  {qid} pages={sorted(set(p['page'] + 1 for p in used))} segs={len(used)} "
              f"choices={'ok' if choices_ok else 'X'} vis={visual} {text_source} {rr}")

    exam = {
        "exam_id": eid, "subject": SUBJECT, "year": meta["year"], "exam_type": meta["exam_type"],
        "exam_period": EXAM_PERIOD_LABEL[meta["exam_type"]],
        "source_file": src_name, "source_sha256": src_sha, "pipeline_version": PIPELINE_VERSION,
        "page_count": doc.page_count, "start_page": first_page + 1, "end_page": max(s["page"] for s in stream) + 1,
        "skipped_pages": [l["page"] + 1 for l in layout if l["page"] < first_page or round(l["w_pt"]) != main_w],
        "anchor_method": anchor_method, "inferred_numbers": inferred,
        "shared_passage_groups": [{k: g[k] for k in ("page", "col", "y", "first", "last", "text")} for g in groups], "missing_numbers": missing,
        "header_metadata": hdr,
        "metadata_verified": hdr["year"] == meta["year"] and hdr["exam_type"] == meta["exam_type"],
        "layout": layout,
        "questions": questions,
    }
    dump_json(out_json, exam)
    return exam


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    files = repo_files()
    todo = []
    for name in sorted(files):
        meta = parse_fname(name)
        if not meta or meta["kind"] != "문제":
            continue
        key = f"{meta['year']}_{meta['exam_type']}"
        if args.only and key not in args.only:
            continue
        todo.append((name, os.path.join(ROOT, files[name]), meta))
    for name, path, meta in todo:
        print(f"== {name}")
        ex = process_exam(name, path, meta, force=args.force)
        print(f"   anchors={ex['anchor_method']} n={len(ex['questions'])} missing={ex['missing_numbers']} "
              f"hdr={ex['header_metadata']['year']},{ex['header_metadata']['exam_type']} verified={ex['metadata_verified']}")


if __name__ == "__main__":
    main()
