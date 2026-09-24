"""PHASE 1: link official answers, run exam/question/answer QA, write JSONL + SQLite + reports."""
import collections
import glob
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p1_common import (ANS_DIR, DB_PATH, Q_DIR, Q_JSON_DIR, REPORT_DIR, ROOT, SUBJECT, dump_json, load_json)

JSONL = os.path.join(Q_DIR, f"{SUBJECT}_questions.jsonl")
EXAMS_JSON = os.path.join(Q_DIR, f"{SUBJECT}_exams.json")


def main():
    keys = {e["exam_id"]: e for e in load_json(os.path.join(ANS_DIR, "answer_keys.json"))["exams"]}
    inventory = load_json(os.path.join(REPORT_DIR, "inventory.json"))
    exams = [load_json(p) for p in sorted(glob.glob(os.path.join(Q_JSON_DIR, f"{SUBJECT}_*.json")))]
    manual_ver = load_json(os.path.join(Q_DIR, "manual_exam_verification.json"), {}) or {}
    errors, warnings = [], []
    questions = []

    exam_rows = []
    for ex in exams:
        eid = ex["exam_id"]
        key = keys.get(eid)
        ex["metadata_verification_method"] = "printed_header_" + ex["header_metadata"]["source"] if ex["metadata_verified"] else None
        mv = manual_ver.get(eid)
        if not ex["metadata_verified"] and mv and mv["year"] == ex["year"] and mv["exam_type"] == ex["exam_type"]:
            ex["metadata_verified"] = True
            ex["metadata_verification_method"] = "manual_visual_header_check"
        nums = [q["question_number"] for q in ex["questions"]]
        dup = sorted(n for n, c in collections.Counter(nums).items() if c > 1)
        missing = [n for n in range(1, 21) if n not in nums]
        ans = (key or {}).get("answers", {})
        ans_ok = sorted(int(k) for k, v in ans.items() if v) == list(range(1, 21)) and \
            all(v in (1, 2, 3, 4, 5) for v in ans.values() if v is not None)
        if missing:
            errors.append({"type": "MISSING_QUESTION", "exam_id": eid, "questions": missing})
        if dup:
            errors.append({"type": "DUPLICATE_QUESTION_NUMBER", "exam_id": eid, "questions": dup})
        if not key:
            errors.append({"type": "NO_ANSWER_SOURCE", "exam_id": eid})
        elif not ans_ok:
            warnings.append({"type": "ANSWER_KEY_INCOMPLETE", "exam_id": eid, "issues": key["issues"]})
        if not ex["metadata_verified"]:
            warnings.append({"type": "EXAM_METADATA_UNVERIFIED", "exam_id": eid, "header": ex["header_metadata"]})
        if key and key["primary_source_kind"] == "COMMENTARY_ANSWER_KEY":
            warnings.append({"type": "ANSWER_FROM_COMMENTARY_KEY", "exam_id": eid, "source": key["primary_source"]})
        for s in (key or {}).get("sources", []):
            if s.get("marker_crosscheck") == "MISMATCH":
                warnings.append({"type": "COMMENTARY_BODY_MISMATCH", "exam_id": eid, "source": s["source_file"],
                                 "questions": s.get("marker_mismatch_questions")})
        pts = (key or {}).get("points", {})
        for q in ex["questions"]:
            qn = str(q["question_number"])
            a = ans.get(qn) if key else None
            q["official_answer"] = a
            q["answer_source_file"] = key["primary_source"] if key and a else None
            q["answer_source_kind"] = key["primary_source_kind"] if key and a else None
            q["answer_sources_agreeing"] = key["n_sources_agreeing"] if key and a else 0
            q["points"] = pts.get(qn) or q.get("points_from_text")
            q["points_source"] = "official_answer_table" if pts.get(qn) else ("stem_[3점]_marker" if q.get("points_from_text") else None)
            if pts.get(qn) and q.get("points_from_text") and pts[qn] != q["points_from_text"]:
                warnings.append({"type": "POINTS_MISMATCH", "question_id": q["question_id"],
                                 "official": pts[qn], "text": q["points_from_text"]})
            rr = list(q["review_reasons"])
            if a is None:
                rr.append("ANSWER_REVIEW_REQUIRED")
            if not ex["metadata_verified"]:
                rr.append("EXAM_METADATA_REVIEW")
            q["review_reasons"] = sorted(set(rr))
            q["review_required"] = bool(q["review_reasons"])
            questions.append(q)
        exam_rows.append({
            "exam_id": eid, "subject": SUBJECT, "year": ex["year"], "exam_type": ex["exam_type"],
            "exam_period": ex["exam_period"], "source_file": ex["source_file"], "source_sha256": ex["source_sha256"],
            "page_count": ex["page_count"], "start_page": ex["start_page"], "end_page": ex["end_page"],
            "skipped_pages": ex["skipped_pages"], "anchor_method": ex["anchor_method"],
            "metadata_verified": ex["metadata_verified"], "header_metadata": ex["header_metadata"],
            "metadata_verification_method": ex["metadata_verification_method"],
            "question_count": len(nums), "q_continuous_1_20": sorted(nums) == list(range(1, 21)),
            "missing_questions": missing, "duplicate_questions": dup,
            "shared_passage_groups": [f"{g['first']}-{g['last']}" for g in ex.get("shared_passage_groups", [])],
            "answer_primary_source": (key or {}).get("primary_source"),
            "answer_primary_source_kind": (key or {}).get("primary_source_kind"),
            "answer_source_files": [s["source_file"] for s in (key or {}).get("sources", [])],
            "official_answer_count": sum(1 for v in ans.values() if v in (1, 2, 3, 4, 5)),
            "answer_issues": (key or {}).get("issues", []),
        })

    # ---- question-level QA
    ids = [q["question_id"] for q in questions]
    for i, c in collections.Counter(ids).items():
        if c > 1:
            errors.append({"type": "DUPLICATE_QUESTION_ID", "question_id": i})
    for q in questions:
        if not q["question_image"] or not os.path.exists(os.path.join(ROOT, q["question_image"])):
            errors.append({"type": "MISSING_PNG", "question_id": q["question_id"]})
        if not q["source_file"] or not q["crop_segments"]:
            errors.append({"type": "NO_SOURCE_MAPPING", "question_id": q["question_id"]})
        if q["official_answer"] is not None and q["official_answer"] not in (1, 2, 3, 4, 5):
            errors.append({"type": "ANSWER_OUT_OF_RANGE", "question_id": q["question_id"]})
        if not q["question_text"].strip():
            warnings.append({"type": "EMPTY_TEXT", "question_id": q["question_id"]})
        if "CHOICE_TEXT_UNRELIABLE" in q["review_reasons"]:
            warnings.append({"type": "CHOICE_TEXT_UNRELIABLE", "question_id": q["question_id"]})
        if "OCR_LOW_CONFIDENCE" in q["review_reasons"]:
            warnings.append({"type": "OCR_LOW_CONFIDENCE", "question_id": q["question_id"]})
        if "VISUAL_FLAG_REVIEW" in q["review_reasons"]:
            warnings.append({"type": "VISUAL_FLAG_UNDETERMINED", "question_id": q["question_id"]})
        if "QUESTION_NUMBER_REVIEW" in q["review_reasons"]:
            warnings.append({"type": "QUESTION_NUMBER_INFERRED", "question_id": q["question_id"]})
        if "CROP_REVIEW" in q["review_reasons"]:
            warnings.append({"type": "CROP_REVIEW", "question_id": q["question_id"]})

    # ---- JSONL / exams json
    questions.sort(key=lambda q: q["question_id"])
    os.makedirs(Q_DIR, exist_ok=True)
    with open(JSONL, "w", encoding="utf-8") as f:
        for q in questions:
            f.write(json.dumps({k: v for k, v in q.items()}, ensure_ascii=False) + "\n")
    dump_json(EXAMS_JSON, exam_rows)

    # ---- SQLite (rebuilt from scratch each run -> deterministic)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript("""
    CREATE TABLE source_files (path TEXT PRIMARY KEY, filename TEXT, category TEXT, role TEXT, extension TEXT,
        size_bytes INTEGER, sha256 TEXT, page_count INTEGER, pdf_kind TEXT, note TEXT);
    CREATE TABLE exams (exam_id TEXT PRIMARY KEY, subject TEXT, year INTEGER, exam_type TEXT, exam_period TEXT,
        source_file TEXT, source_sha256 TEXT, page_count INTEGER, start_page INTEGER, end_page INTEGER,
        anchor_method TEXT, metadata_verified INTEGER, question_count INTEGER, q_continuous_1_20 INTEGER,
        answer_primary_source TEXT, answer_primary_source_kind TEXT, official_answer_count INTEGER, extra_json TEXT);
    CREATE TABLE questions (question_id TEXT PRIMARY KEY, subject TEXT, exam_id TEXT REFERENCES exams(exam_id),
        year INTEGER, exam_type TEXT, exam_period TEXT, question_number INTEGER,
        source_file TEXT, source_sha256 TEXT, source_page INTEGER, original_page INTEGER, source_pages TEXT,
        crop_segments TEXT, question_text TEXT, shared_passage_text TEXT, stem TEXT, passage TEXT,
        embedded_image_ocr_text TEXT, text_source TEXT, choices_json TEXT, n_choices INTEGER,
        official_answer INTEGER CHECK (official_answer IS NULL OR official_answer BETWEEN 1 AND 5),
        answer_source_file TEXT, answer_source_kind TEXT, points INTEGER, points_source TEXT,
        has_visual_material INTEGER, visual_detail TEXT, shared_passage_group TEXT,
        question_image TEXT, question_image_sha256 TEXT, review_required INTEGER, review_reasons TEXT,
        UNIQUE(exam_id, question_number));
    CREATE TABLE choices (question_id TEXT REFERENCES questions(question_id), number INTEGER, text TEXT,
        PRIMARY KEY (question_id, number));
    CREATE TABLE answer_sources (exam_id TEXT, source_file TEXT, source_kind TEXT, sha256 TEXT, method TEXT,
        answers_json TEXT, complete INTEGER, crosscheck TEXT, PRIMARY KEY (exam_id, source_file));
    CREATE TABLE review_items (question_id TEXT, reason TEXT, PRIMARY KEY (question_id, reason));
    """)
    for r in inventory:
        cur.execute("INSERT INTO source_files VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (r["path"], r["filename"], r["category"], r["role"], r["extension"], r["size_bytes"], r["sha256"],
                     r["page_count"], r["pdf_kind"], r.get("note")))
    for e in exam_rows:
        cur.execute("INSERT INTO exams VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (e["exam_id"], e["subject"], e["year"], e["exam_type"], e["exam_period"], e["source_file"],
                     e["source_sha256"], e["page_count"], e["start_page"], e["end_page"], e["anchor_method"],
                     int(e["metadata_verified"]), e["question_count"], int(e["q_continuous_1_20"]),
                     e["answer_primary_source"], e["answer_primary_source_kind"], e["official_answer_count"],
                     json.dumps({k: e[k] for k in ("skipped_pages", "header_metadata", "missing_questions",
                                                   "duplicate_questions", "shared_passage_groups",
                                                   "answer_source_files", "answer_issues")}, ensure_ascii=False)))
    for q in questions:
        cur.execute("INSERT INTO questions VALUES (" + ",".join("?" * 33) + ")", (
            q["question_id"], q["subject"], q["exam_id"], q["year"], q["exam_type"], q["exam_period"],
            q["question_number"], q["source_file"], q["source_sha256"], q["source_page"], q["original_page"],
            json.dumps(q["source_pages"]), json.dumps(q["crop_segments"], ensure_ascii=False), q["question_text"],
            q.get("shared_passage_text", ""), q["stem"], q["passage"], q.get("embedded_image_ocr_text", ""),
            q["text_source"], json.dumps(q["choices"], ensure_ascii=False), len(q["choices"]),
            q["official_answer"], q["answer_source_file"], q["answer_source_kind"], q["points"], q["points_source"],
            None if q["has_visual_material"] is None else int(q["has_visual_material"]),
            json.dumps(q["visual_detail"]),
            json.dumps(q.get("shared_passage_group"), ensure_ascii=False) if q.get("shared_passage_group") else None,
            q["question_image"], q["question_image_sha256"], int(q["review_required"]),
            json.dumps(q["review_reasons"])))
        for c in q["choices"]:
            cur.execute("INSERT INTO choices VALUES (?,?,?)", (q["question_id"], c["number"], c["text"]))
        for r_ in q["review_reasons"]:
            cur.execute("INSERT INTO review_items VALUES (?,?)", (q["question_id"], r_))
    for eid, k in sorted(keys.items()):
        for s in k["sources"]:
            cc = s.get("marker_crosscheck") or s.get("text_layer_crosscheck")
            cur.execute("INSERT INTO answer_sources VALUES (?,?,?,?,?,?,?,?)",
                        (eid, s["source_file"], s["source_kind"], s["sha256"], s["method"],
                         json.dumps(s["answers"]), int(s["complete"]), cc))
    con.commit()
    integrity = cur.execute("PRAGMA integrity_check").fetchone()[0]
    n_rows = cur.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    con.close()
    if integrity != "ok":
        errors.append({"type": "DB_INTEGRITY", "detail": integrity})

    # ---- QA summaries
    n = len(questions)
    linked = sum(1 for q in questions if q["official_answer"] is not None)
    reasons = collections.Counter(r for q in questions for r in q["review_reasons"])
    vis = collections.Counter(str(q["has_visual_material"]) for q in questions)
    choice_counts = collections.Counter(len(q["choices"]) for q in questions)
    per_exam_answers = {e["exam_id"]: e["official_answer_count"] for e in exam_rows}
    inv_cats = collections.Counter(r["category"] for r in inventory)
    qa = {
        "sources": {
            "inventory_categories": dict(inv_cats),
            "problem_pdfs": [r["path"] for r in inventory if r["category"] == "A_SOCIOCULTURE_PROBLEM"],
            "answer_files": [r["path"] for r in inventory if r["category"] == "B_SOCIOCULTURE_ANSWER"],
            "official_answer_tables": [r["path"] for r in inventory if r["role"] == "answer_key"],
            "commentary_files": [r["path"] for r in inventory if r["role"] == "answer_explanation"],
            "reference_files": [dict(path=r["path"], note=r.get("note")) for r in inventory
                                if r["category"] == "C_REFERENCE_2028"],
        },
        "extraction": {
            "source_problem_pdfs": len(exams),
            "total_pages": sum(e["page_count"] for e in exam_rows),
            "exams": len(exam_rows),
            "questions": n,
            "exams_complete_1_20": sum(1 for e in exam_rows if e["q_continuous_1_20"]),
            "anchor_methods": dict(collections.Counter(e["anchor_method"] for e in exam_rows)),
            "text_sources": dict(collections.Counter(q["text_source"] for q in questions)),
            "shared_passage_questions": sum(1 for q in questions if q.get("shared_passage_group")),
        },
        "answers": {
            "total_questions": n, "official_answers_linked": linked, "official_answer_null": n - linked,
            "answer_values_outside_1_5": sum(1 for q in questions if q["official_answer"] not in (None, 1, 2, 3, 4, 5)),
            "per_exam_answer_count": per_exam_answers,
            "answer_source_kind_by_question": dict(collections.Counter(str(q["answer_source_kind"]) for q in questions)),
            "exams_by_primary_source_kind": dict(collections.Counter(e["answer_primary_source_kind"] for e in exam_rows)),
            "exams_with_multiple_agreeing_sources": sum(1 for k in keys.values() if k["n_sources_agreeing"] > 1),
            "answer_review_required": reasons.get("ANSWER_REVIEW_REQUIRED", 0),
        },
        "quality": {
            "normal": sum(1 for q in questions if not q["review_required"]),
            "review_required": sum(1 for q in questions if q["review_required"]),
            "review_reasons": dict(reasons),
            "ERROR": len(errors), "WARNING": len(warnings),
            "warning_types": dict(collections.Counter(w["type"] for w in warnings)),
            "has_visual_material": dict(vis),
            "choice_count_distribution": {str(k): v for k, v in sorted(choice_counts.items())},
            "unique_question_ids": len(set(ids)),
        },
        "assets": {
            "exam_json_files": len(exams), "jsonl_files": 1, "jsonl_rows": n,
            "png_files": len(glob.glob(os.path.join(Q_DIR, "images", "*.png"))),
            "sqlite_question_rows": n_rows, "sqlite_integrity": integrity,
            "db_path": os.path.relpath(DB_PATH, ROOT), "jsonl_path": os.path.relpath(JSONL, ROOT),
        },
    }
    dump_json(os.path.join(REPORT_DIR, "qa_summary.json"), qa)
    dump_json(os.path.join(REPORT_DIR, "qa_exams.json"), exam_rows)
    dump_json(os.path.join(REPORT_DIR, "errors.json"), errors)
    dump_json(os.path.join(REPORT_DIR, "warnings.json"), warnings)
    review = [{"question_id": q["question_id"], "reasons": q["review_reasons"], "image": q["question_image"]}
              for q in questions if q["review_required"]]
    dump_json(os.path.join(REPORT_DIR, "review_list.json"), review)
    print(json.dumps({k: qa[k] for k in ("extraction", "answers", "quality", "assets")}, ensure_ascii=False, indent=1))
    print("ERRORS:", errors[:20])


if __name__ == "__main__":
    main()
