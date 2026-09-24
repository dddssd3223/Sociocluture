#!/usr/bin/env python3
"""SOCIOCULTURE Phase 2B: LOW rescue / false-negative audit.

Reads Phase 1 questions and Phase 2A outputs (read-only). Review set:
  - every Phase 2A LOW question
  - NONE_AUDIT: Phase 2A NONE narrowed deterministically by metadata
Decisions come from data/phase2/SOCIOCULTURE/phase2b_rescue_decisions.tsv.

  python3 scripts/p2b_rescue.py --dump-batch N [--batch-size 50]
  python3 scripts/p2b_rescue.py            # build outputs + QA
"""
import argparse
import collections
import csv
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from p1_common import ROOT, dump_json  # noqa: E402
from p2a_classify import load_questions, norm, parts  # noqa: E402

P2 = os.path.join(ROOT, "data/phase2/SOCIOCULTURE")
P2A = os.path.join(P2, "phase2a_relevance.jsonl")
P2A_SUMMARY = os.path.join(P2, "phase2a_relevance_summary.json")
MASTER = os.path.join(ROOT, "reference/curriculum/integrated_social_official_scope.json")
DECISIONS = os.path.join(P2, "phase2b_rescue_decisions.tsv")
OUT_REVIEW = os.path.join(P2, "phase2b_rescue_review.jsonl")
OUT_INDEX = os.path.join(P2, "phase2c_candidate_index.jsonl")
OUT_SUMMARY = os.path.join(P2, "phase2b_rescue_summary.json")
PREFIX = "SOCIOCULTURE_"
DECISION_VALUES = {"RESCUE", "REMAIN_LOW", "REMAIN_NONE"}
NONE_AUDIT_CAP = 50

# Direct 통합사회 concepts that, if recorded in a NONE item's required_content,
# send it to the safety audit.
AUDIT_CONTENT_KW = re.compile(
    "빈곤|복지|사회 보장|소수자|차별|인구|고령|저출산|세계화|정보화|정보 격차|도시화|다문화|이주|"
    "문화 상대|자문화|전파|문화 변동|분배 정책|불평등|인권|환경|성별|성 불평등|노동|지역")

DOMAIN_OF = {"1-01": "기타", "1-02": "기타", "1-03": "기타", "1-04": "문화와 다양성",
             "1-05": "생활공간과 사회", "2-01": "인권보장과 헌법", "2-02": "사회정의와 불평등",
             "2-03": "시장경제와 지속가능발전", "2-04": "세계화와 평화", "2-05": "미래와 지속가능한 삶"}


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_p2a():
    with open(P2A, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def none_audit_reasons(r):
    rf = r["rule_first_pass"]
    why = []
    if r["curriculum_codes"]:
        why.append("curriculum_codes_present")
    if rf["rule_decision"] == "DIRECT":
        why.append("rule_first_pass_DIRECT")
    if rf["rule_decision"] == "ADAPTABLE" and rf["I"] >= 3:
        why.append("rule_first_pass_ADAPTABLE_strong(I>=3)")
    if r["phase2a_review_reasons"]:
        why.append("phase2a_flags:" + ",".join(r["phase2a_review_reasons"]))
    if AUDIT_CONTENT_KW.search(" ".join(r["required_content"])):
        why.append("required_content_direct_concept")
    return why


def review_set(p2a):
    low = [r for r in p2a if r["phase2a_decision"] == "LOW"]
    audit = [(r, none_audit_reasons(r)) for r in p2a if r["phase2a_decision"] == "NONE"]
    audit = [(r, w) for r, w in audit if w]
    return low, audit


def dump_batch(idx, size):
    qs = {q["question_id"]: q for q in load_questions()}
    low, audit = review_set(load_p2a())
    items = [(r, None) for r in low] + audit
    batch = items[idx * size:(idx + 1) * size]
    print(f"# batch {idx} ({len(batch)} of {len(items)}; LOW {len(low)}, NONE_AUDIT {len(audit)})")
    for r, why in batch:
        q = qs[r["question_id"]]
        p = parts(q)
        ch = " / ".join(f"{c['number']}{'*' if c['number'] == q['official_answer'] else ''}{norm(c['text'])[:55]}"
                        for c in q["choices"]) or ("ANS=" + str(q["official_answer"]) + " " + norm(q["question_text"])[150:450])
        tag = "LOW" if why is None else "NONE_AUDIT(" + ";".join(why) + ")"
        print(f"{r['question_id'][13:]} {tag} codes={','.join(c[4:] for c in r['curriculum_codes'])} "
              f"flags={','.join(r['phase2a_review_reasons'])}\n A:{'; '.join(r['required_content'])} | {r['adaptation_note']}"
              f"\n S:{norm(p['stem'])[:120]}\n B:{norm(p['body'])[:260]}\n C:{ch}")


def read_decisions():
    out = {}
    with open(DECISIONS, encoding="utf-8") as f:
        for row in csv.reader((l for l in f if l.strip() and not l.startswith("#")), delimiter="\t"):
            row += [""] * (7 - len(row))
            qid, dec, codes, content, basis, relevance, flags = row[:7]
            qid = qid if qid.startswith(PREFIX) else PREFIX + qid
            if qid in out:
                raise SystemExit(f"duplicate decision row: {qid}")
            out[qid] = {
                "decision": dec.strip(),
                "codes": ["10통사" + c.strip() if not c.strip().startswith("10통사") else c.strip()
                          for c in codes.split(",") if c.strip()],
                "content": [c.strip() for c in content.split(";") if c.strip()],
                "basis": basis.strip(),
                "relevance": relevance.strip(),
                "flags": [x.strip() for x in flags.split(",") if x.strip()],
            }
    return out


def kice_support(codes, master):
    std = {s["standard_code"]: s for s in master["standards"]}
    sup = []
    for c in codes:
        for n in std.get(c, {}).get("example_questions_2028", []):
            sup.append({"question_number": n, "mapped_standard": c,
                        "confirmed_depth": "2028 예시문항에서 해당 성취기준 평가 확인(SOURCE B 부록 매핑); "
                                           "문항별 깊이 대조는 Phase 2C"})
    return sup


def build():
    p2a = load_p2a()
    master = json.load(open(MASTER, encoding="utf-8"))
    p2a_summary = json.load(open(P2A_SUMMARY, encoding="utf-8"))
    qs = {q["question_id"]: q for q in load_questions()}
    dec = read_decisions()
    low, audit = review_set(p2a)
    if len(audit) > NONE_AUDIT_CAP:
        raise SystemExit(f"NONE_AUDIT too large: {len(audit)}")
    counts = collections.Counter(r["phase2a_decision"] for r in p2a)
    std_codes = {s["standard_code"] for s in master["standards"]}

    review_rows = []
    for r, why in [(r, None) for r in low] + audit:
        qid = r["question_id"]
        d = dec.get(qid)
        if d is None:
            raise SystemExit(f"missing decision: {qid}")
        codes = d["codes"] or r["curriculum_codes"]
        rr = list(d["flags"])
        review_rows.append({
            "question_id": qid,
            "phase2a_decision": r["phase2a_decision"],
            "phase2b_decision": d["decision"],
            "curriculum_codes": codes,
            "primary_domain": DOMAIN_OF[codes[0][4:8]] if codes else "기타",
            "required_content": d["content"] or r["required_content"],
            "rescue_basis": d["basis"],
            "kice_example_support": kice_support(codes, master) if d["decision"] == "RESCUE" else [],
            "content_relevance": d["relevance"],
            "none_audit_reasons": why or [],
            "phase2a_rationale": r["adaptation_note"],
            "official_answer": r["official_answer"],
            "review_required": bool(rr),
            "review_reasons": rr,
        })
    extra = set(dec) - {x["question_id"] for x in review_rows}

    with open(OUT_REVIEW, "w", encoding="utf-8") as f:
        for x in review_rows:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    rev_by_id = {x["question_id"]: x for x in review_rows}
    index = []
    for r in p2a:
        src = {"DIRECT": "PHASE2A_DIRECT", "ADAPTABLE": "PHASE2A_ADAPTABLE"}.get(r["phase2a_decision"])
        rv = rev_by_id.get(r["question_id"])
        if src is None and rv and rv["phase2b_decision"] == "RESCUE":
            src = "PHASE2B_RESCUE"
        if src is None:
            continue
        codes = rv["curriculum_codes"] if src == "PHASE2B_RESCUE" else r["curriculum_codes"]
        index.append({
            "question_id": r["question_id"],
            "candidate_source": src,
            "curriculum_codes": codes,
            "primary_domain": rv["primary_domain"] if src == "PHASE2B_RESCUE" else r["primary_domain"],
            "phase1_review_required": r["phase1_review_required"],
            "phase2a_review_required": r["phase2a_review_required"],
            "phase2b_review_required": bool(rv and rv["review_required"]),
        })
    with open(OUT_INDEX, "w", encoding="utf-8") as f:
        for x in index:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    low_rows = [x for x in review_rows if x["phase2a_decision"] == "LOW"]
    aud_rows = [x for x in review_rows if x["phase2a_decision"] == "NONE"]
    lc = collections.Counter(x["phase2b_decision"] for x in low_rows)
    ac = collections.Counter(x["phase2b_decision"] for x in aud_rows)
    rescues = [x for x in review_rows if x["phase2b_decision"] == "RESCUE"]
    by_std = collections.Counter(c for x in rescues for c in x["curriculum_codes"])
    ids = [x["question_id"] for x in index]
    src_ids = {r["question_id"] for r in p2a}
    answers_changed = sum(1 for x in review_rows if x["official_answer"] != qs[x["question_id"]]["official_answer"])
    qa = {
        "phase2a_input": len(p2a),
        "phase2a_counts_from_file": dict(counts),
        "phase2a_counts_match_summary": all(counts[k] == p2a_summary[k] for k in ("DIRECT", "ADAPTABLE", "LOW", "NONE")),
        "low_reviewed": len(low_rows),
        "low_decision_sum_ok": lc["RESCUE"] + lc["REMAIN_LOW"] == len(low) and len(low_rows) == counts["LOW"],
        "none_audit_count": len(aud_rows),
        "none_audit_decision_sum_ok": sum(ac.values()) == len(audit),
        "invalid_decisions": sum(1 for x in review_rows if x["phase2b_decision"] not in DECISION_VALUES
                                 or (x["phase2a_decision"] == "LOW" and x["phase2b_decision"] == "REMAIN_NONE")),
        "decision_rows_not_in_review_set": sorted(extra),
        "phase2c_candidate_count": len(index),
        "phase2c_count_ok": len(index) == counts["DIRECT"] + counts["ADAPTABLE"] + len(rescues),
        "candidate_duplicate_ids": len(ids) - len(set(ids)),
        "candidate_missing_source_ids": len(set(ids) - src_ids),
        "rescue_without_curriculum_evidence": sum(1 for x in rescues if not x["curriculum_codes"]
                                                  or not set(x["curriculum_codes"]) <= std_codes),
        "rescue_without_rationale": sum(1 for x in rescues if not x["content_relevance"] or not x["rescue_basis"]),
        "official_answers_modified": answers_changed,
    }
    summary = {
        "phase2a_direct": counts["DIRECT"], "phase2a_adaptable": counts["ADAPTABLE"],
        "phase2a_low": counts["LOW"], "phase2a_none": counts["NONE"],
        "low_reviewed": len(low_rows), "low_rescue": lc["RESCUE"], "low_remain_low": lc["REMAIN_LOW"],
        "none_audit_rule": "NONE with curriculum_codes, rule_first_pass DIRECT, rule_first_pass ADAPTABLE with I>=3, "
                           "any Phase 2A review flag, or a direct 통합사회 concept in required_content",
        "none_audit_count": len(aud_rows), "none_audit_rescue": ac["RESCUE"],
        "none_audit_remain_none": ac["REMAIN_NONE"], "none_audit_remain_low": ac["REMAIN_LOW"],
        "total_rescue": len(rescues),
        "phase2c_candidate_count": len(index),
        "phase2c_candidate_by_source": dict(collections.Counter(x["candidate_source"] for x in index)),
        "rescue_by_domain": dict(collections.Counter(x["primary_domain"] for x in rescues).most_common()),
        "rescue_by_standard": dict(by_std.most_common()),
        "phase2b_review_required": sum(1 for x in review_rows if x["review_required"]),
        "qa": qa,
        "inputs": {os.path.relpath(p, ROOT): sha(p) for p in (P2A, P2A_SUMMARY, MASTER, DECISIONS)},
    }
    dump_json(OUT_SUMMARY, summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "inputs"}, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-batch", type=int)
    ap.add_argument("--batch-size", type=int, default=50)
    a = ap.parse_args()
    if a.dump_batch is not None:
        dump_batch(a.dump_batch, a.batch_size)
    else:
        build()


if __name__ == "__main__":
    main()
