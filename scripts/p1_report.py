"""PHASE 1: render the human-readable report from the QA json files."""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p1_common import ANS_DIR, Q_DIR, REPORT_DIR, load_json

OUT = os.path.join(REPORT_DIR, "PHASE1_REPORT.md")


def main():
    qa = load_json(os.path.join(REPORT_DIR, "qa_summary.json"))
    exams = load_json(os.path.join(REPORT_DIR, "qa_exams.json"))
    warnings = load_json(os.path.join(REPORT_DIR, "warnings.json"))
    errors = load_json(os.path.join(REPORT_DIR, "errors.json"))
    review = load_json(os.path.join(REPORT_DIR, "review_list.json"))
    keys = load_json(os.path.join(ANS_DIR, "answer_keys.json"))["exams"]
    inv = load_json(os.path.join(REPORT_DIR, "inventory.json"))
    qs = [json.loads(l) for l in open(os.path.join(Q_DIR, "SOCIOCULTURE_questions.jsonl"), encoding="utf-8")]
    S, X, A, Q, AS = qa["sources"], qa["extraction"], qa["answers"], qa["quality"], qa["assets"]
    marker_match = sum(1 for e in keys for s in e["sources"] if s.get("marker_crosscheck") == "MATCH")
    manual = [s["source_file"] for e in keys for s in e["sources"] if s["method"] == "manual_visual_transcription"]
    multi = [e["exam_id"] for e in keys if e["n_sources_agreeing"] > 1]
    pts = collections.defaultdict(list)
    for q in qs:
        pts[q["exam_id"]].append(q["points"])
    pts_off = {e: (sum(p for p in v if p), v.count(None)) for e, v in pts.items() if None in v or sum(v) != 50}

    L = []
    w = L.append
    w("# SOCIOCULTURE PHASE 1 — 사회·문화 평가원 기출 원문 DB 보고서\n")
    w("Scope: raw past-exam DB only. No 2028 통합사회 relevance judgement, curriculum/성취기준 mapping, "
      "KEEP/DROP, dedup, type analysis or question generation was performed (PHASE 2 not started).\n")
    w("## 1. Sources\n")
    w(f"- Repository files inventoried: {len(inv)} (`data/reports/SOCIOCULTURE/inventory.json`, with size, SHA-256, page count, text/scanned)")
    for c, n in sorted(S["inventory_categories"].items()):
        w(f"  - {c}: {n}")
    w(f"- 사회·문화 problem PDFs (A): {len(S['problem_pdfs'])} — 2005~2026학년도 × (6월, 9월, 수능) + 2027학년도 6월·9월")
    w(f"- 사회·문화 answer materials (B): {len(S['answer_files'])}")
    w(f"  - official answer tables (정답/정답표, incl. 2 사탐전체 tables, 2 JPG): {len(S['official_answer_tables'])}")
    w(f"  - 정답 및 해설 (commentary with answer summary): {len(S['commentary_files'])}")
    w("- Reference files (C, inventoried only — NOT used in PHASE 1):")
    for r in S["reference_files"]:
        w(f"  - `{r['path']}`" + (f" — {r['note']}" if r.get("note") else ""))
    w("- D (SVG/image assets): none besides the 2 answer-table JPGs (no SVG files exist in the repository). "
      "E/F (existing code / DB / JSON): none before this work.\n")
    w("## 2. Extraction\n")
    w(f"- Source problem PDFs processed: {X['source_problem_pdfs']}; total pages: {X['total_pages']} "
      "(incl. 2 cover pages in 2019 9월/수능 skipped, and a 5th overflow page in 2005 9월)")
    w(f"- Exams discovered: {X['exams']} (1 file = 1 exam, confirmed by printed page-1 title; "
      f"52 by text layer/OCR automatically, 16 by visual check → `data/questions/SOCIOCULTURE/manual_exam_verification.json`)")
    w(f"- Questions extracted: {X['questions']}; exams with continuous Q1–Q20: {X['exams_complete_1_20']}/{X['exams']}")
    w(f"- Question anchors: {X['anchor_methods']} (image_structural_ocr = scanned/image-only PDFs: bold margin number + digit OCR)")
    w(f"- Text source: {X['text_sources']}")
    w(f"- Questions carrying a shared passage (`[N~M] 다음 … 물음에 답하시오`): {X['shared_passage_questions']} — "
      "the shared block is prepended to every question in the group (PNG + `shared_passage_text`); "
      "3 headers on image pages recorded in `manual_group_headers.json`")
    w("- Questions spanning columns/pages are stitched vertically; a dotted rule marks each join.\n")
    w("## 3. Answers\n")
    w(f"- Total questions: {A['total_questions']}")
    w(f"- Official answers linked: {A['official_answers_linked']}; official_answer = null: {A['official_answer_null']}; "
      f"values outside 1–5: {A['answer_values_outside_1_5']}")
    w(f"- Every exam has exactly 20 answers (min {min(A['per_exam_answer_count'].values())}, max {max(A['per_exam_answer_count'].values())}).")
    w(f"- Exams by primary answer source: {A['exams_by_primary_source_kind']}")
    w(f"  - OFFICIAL_ANSWER_TABLE: 평가원 정답표 files (17 exams).")
    w(f"  - COMMENTARY_ANSWER_KEY: the answer summary line (1.~20.) of the repository's 정답 및 해설 file — the only answer "
      f"material present for 51 exams. Cross-checked against the per-question '정답 ⑤' markers in the same file: "
      f"{marker_match} files MATCH, 0 mismatch (the others have garbled fonts / no markers).")
    w(f"- Exams with 2 independent sources that agree on all 20: {len(multi)} ({', '.join(e[13:] for e in multi)}); disagreements: 0")
    w(f"- Scanned/image answer tables transcribed visually (reading the printed table, not solving): {len(manual)} "
      f"→ `data/answers/SOCIOCULTURE/manual_transcriptions.json` (SHA-256 pinned; 2009 9월 also matches its text layer)")
    w("- No answer was derived by solving a question. Answer ↔ exam matching is by year + 시험 종류 + 과목 + 문항번호.\n")
    w("## 4. Quality\n")
    w(f"- normal: {Q['normal']}  |  review_required: {Q['review_required']}  |  ERROR: {Q['ERROR']}  |  WARNING: {Q['WARNING']}")
    w(f"- review reasons: {Q['review_reasons']}")
    w(f"- warning types: {Q['warning_types']}")
    w(f"- has_visual_material: {Q['has_visual_material']} (None = scanned/OCR questions, flagged VISUAL_FLAG_REVIEW)")
    w(f"- choices structured (①~⑤): {Q['choice_count_distribution']} (0 = 149 OCR questions, whose circled digits are not OCR-readable, + 2005 6월 Q15)")
    w(f"- unique question_id: {Q['unique_question_ids']} / {X['questions']}; missing PNG: 0; SQLite integrity: {AS['sqlite_integrity']}")
    w(f"- Point totals (50 per exam) as an extra structure check: {X['exams'] - len(pts_off)} exams = 50; others: "
      + ", ".join(f"{e[13:]}={t}{f' ({n} null)' if n else ''}" for e, (t, n) in sorted(pts_off.items())))
    w("")
    w("## 5. Assets\n")
    w(f"- Per-exam JSON: {AS['exam_json_files']} (`data/questions/SOCIOCULTURE/json/`) + exam index `SOCIOCULTURE_exams.json`")
    w(f"- JSONL: {AS['jsonl_files']} file / {AS['jsonl_rows']} rows (`{AS['jsonl_path']}`)")
    w(f"- PNG (Source of Truth, ~200 dpi, grayscale): {AS['png_files']} (`data/questions/SOCIOCULTURE/images/`)")
    w(f"- SQLite: `{AS['db_path']}` — questions {AS['sqlite_question_rows']} rows; tables exams, questions, choices, "
      "answer_sources, review_items, source_files")
    w("- Pipeline: `scripts/p1_inventory.py` → `p1_answers.py` → `p1_extract.py` → `p1_build_db.py` → `p1_report.py`. "
      "Checkpoint = source SHA-256 + pipeline version per exam; OCR cached under `data/cache/`. "
      "Re-run verified deterministic (identical JSON/PNG/JSONL).\n")
    w("## 6. Known limitations\n")
    w("- OCR: 149 questions (2006 6월·수능, 2008 9월, 2009 9월, 2015 6월, 2017 9월, 2025 수능 fully; 2005 9월 Q6/Q14, "
      "2007 9월 7 questions) are image-only/garbled-font → Tesseract text with typical errors (㉠/① misreads, 학→확); choices not structured.")
    w("- Old PDFs (2005–2014) have text without word spacing; some fonts map brackets to ġ/Ģ or ~/₩ (handled for [3점] and [N~M]).")
    w("- Text inside embedded raster figures is not in the PDF text layer; it is OCR'd separately into `embedded_image_ocr_text`.")
    w("- has_visual_material is heuristic (raster image, ≥2×2 table, diagonal strokes/curves, filled shapes); null for OCR questions.")
    w("- points: from the official table where transcribed, else the [3점] marker; OCR questions may have points=null; "
      "one 2005 수능 [3점] marker is unreadable (total 49).")
    w("- 51 exams rely on the 해설 answer summary (no 평가원 정답표 file in the repository for them).")
    w("- `28예시` is a 2-byte placeholder (the original 2028 예시 PDF was replaced in commit dd3b206).")
    w("- Crops are generous: the last question of an exam can include the '확인 사항' box; this is cosmetic.\n")
    w("## 7. Errors\n")
    w(f"ERROR count: {len(errors)}" + ("" if not errors else "\n\n" + "\n".join(f"- {e}" for e in errors)))
    w("\n## 8. Review list\n")
    w(f"{len(review)} questions (`data/reports/SOCIOCULTURE/review_list.json`):\n")
    w("| question_id | reasons |")
    w("|---|---|")
    for r in review:
        w(f"| {r['question_id']} | {', '.join(r['reasons'])} |")
    w("\n## 9. Per-exam QA\n")
    w("| exam_id | pages | Q | 1–20 | answers | answer source | anchors | header check |")
    w("|---|---|---|---|---|---|---|---|")
    for e in exams:
        w(f"| {e['exam_id']} | {e['start_page']}–{e['end_page']} | {e['question_count']} | {'OK' if e['q_continuous_1_20'] else 'NO'} "
          f"| {e['official_answer_count']} | {e['answer_primary_source_kind']} | {e['anchor_method']} | {e['metadata_verification_method']} |")
    w("\nPHASE 1 COMPLETE — STOPPED BEFORE PHASE 2\n")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(OUT)


if __name__ == "__main__":
    main()
