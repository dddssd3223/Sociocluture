#!/usr/bin/env python3
"""SOCIOCULTURE Phase 2C: final content curation (KEEP / DROP) of the Phase 2C candidates.

Inputs (read-only): Phase 1 questions, Phase 2A relevance, Phase 2B review, Phase 2C candidate
index, curriculum master. Decisions: data/phase2/SOCIOCULTURE/phase2c_final_decisions.tsv

  python3 scripts/p2c_curate.py --dump-batch N [--batch-size 46]
  python3 scripts/p2c_curate.py            # build outputs + QA
"""
import argparse
import collections
import csv
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from p1_common import ROOT, dump_json  # noqa: E402
from p2a_classify import load_questions, norm, parts  # noqa: E402

P2 = os.path.join(ROOT, "data/phase2/SOCIOCULTURE")
P2A = os.path.join(P2, "phase2a_relevance.jsonl")
P2B = os.path.join(P2, "phase2b_rescue_review.jsonl")
INDEX = os.path.join(P2, "phase2c_candidate_index.jsonl")
MASTER = os.path.join(ROOT, "reference/curriculum/integrated_social_official_scope.json")
DECISIONS = os.path.join(P2, "phase2c_final_decisions.tsv")
OUT_CUR = os.path.join(P2, "phase2c_final_curation.jsonl")
OUT_KEEP = os.path.join(P2, "final_content_asset_index.jsonl")
OUT_SUMMARY = os.path.join(P2, "phase2c_final_curation_summary.json")
PREFIX = "SOCIOCULTURE_"
ADAPT = {"NONE", "LIGHT", "SUBSTANTIAL"}
DROP_REASONS = {"SOCIOCULTURE_RESEARCH_METHOD", "SOCIOCULTURE_THEORY", "SOCIOCULTURE_CLASSIFICATION",
                "SOCIOCULTURE_CALCULATION", "CONTENT_ONLY_INDIRECT", "DATA_ONLY",
                "REQUIRES_SUBSTANTIAL_REWRITE", "OUTSIDE_CURRICULUM_DEPTH", "OTHER"}
DOMAIN_OF = {"1-01": "기타", "1-02": "기타", "1-03": "기타", "1-04": "문화와 다양성",
             "1-05": "생활공간과 사회", "2-01": "인권보장과 헌법", "2-02": "사회정의와 불평등",
             "2-03": "시장경제와 지속가능발전", "2-04": "세계화와 평화", "2-05": "미래와 지속가능한 삶"}
# 2028 예시문항 whose published 출제 의도 we checked for depth (SOURCE B pp.57-64)
KICE_DEPTH_NOTES = {
    "10통사2-02-03": (13, "예시 13번: 사회 복지 제도(기초 연금), 적극적 평등 실현 조치, 지역 격차 완화 정책을 사례로 평가"),
    "10통사2-01-03": (12, "예시 12번: 난민 통계 비율 분석과 사회적 소수자 개념 평가"),
    "10통사2-04-02": (12, "예시 12번: 국제기구·비정부 기구의 역할 평가"),
    "10통사2-05-01": (24, "예시 24번: 인구 구조 자료 분석(부록 매핑)"),
}


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def jl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def dump_batch(idx, size):
    qs = {q["question_id"]: q for q in load_questions()}
    p2a = {r["question_id"]: r for r in jl(P2A)}
    p2b = {r["question_id"]: r for r in jl(P2B)}
    cand = jl(INDEX)
    batch = cand[idx * size:(idx + 1) * size]
    print(f"# batch {idx} ({len(batch)} of {len(cand)})")
    for c in batch:
        qid = c["question_id"]
        q, a = qs[qid], p2a[qid]
        p = parts(q)
        ch = " / ".join(f"{x['number']}{'*' if x['number'] == q['official_answer'] else ''}{norm(x['text'])[:60]}"
                        for x in q["choices"]) or ("ANS=" + str(q["official_answer"]) + " " + norm(q["question_text"])[150:470])
        extra = ""
        if qid in p2b:
            extra = " | 2B:" + p2b[qid]["content_relevance"]
        print(f"{qid[13:]} {c['candidate_source'][7:]} codes={','.join(x[4:] for x in c['curriculum_codes'])} "
              f"flags={','.join(a['phase2a_review_reasons'])}\n A:{'; '.join(a['required_content'])} | {a['adaptation_note']}{extra}"
              f"\n S:{norm(p['stem'])[:110]}\n B:{norm(p['body'])[:240]}\n C:{ch}")


def read_decisions():
    out = {}
    with open(DECISIONS, encoding="utf-8") as f:
        for row in csv.reader((l for l in f if l.strip() and not l.startswith("#")), delimiter="\t"):
            row += [""] * (9 - len(row))
            qid, dec, adapt, codes, content, reason, note, drops, flags = [x.strip() for x in row[:9]]
            qid = qid if qid.startswith(PREFIX) else PREFIX + qid
            if qid in out:
                raise SystemExit(f"duplicate decision row: {qid}")
            out[qid] = {
                "decision": dec, "adapt": adapt,
                "codes": [c if c.startswith("10통사") else "10통사" + c for c in (x.strip() for x in codes.split(",")) if c],
                "content": [c.strip() for c in content.split(";") if c.strip()],
                "reason": reason, "note": note,
                "drops": [x.strip() for x in drops.split(",") if x.strip()],
                "flags": [x.strip() for x in flags.split(",") if x.strip()],
            }
    return out


def build():
    qs = {q["question_id"]: q for q in load_questions()}
    p2a = {r["question_id"]: r for r in jl(P2A)}
    p2b = {r["question_id"]: r for r in jl(P2B)}
    cand = jl(INDEX)
    master = json.load(open(MASTER, encoding="utf-8"))
    std = {s["standard_code"] for s in master["standards"]}
    dec = read_decisions()

    rows = []
    for c in cand:
        qid = c["question_id"]
        d = dec.get(qid)
        if d is None:
            raise SystemExit(f"missing decision: {qid}")
        codes = d["codes"] or c["curriculum_codes"]
        kice = []
        if d["decision"] == "KEEP":
            for code in codes:
                if code in KICE_DEPTH_NOTES:
                    n, why = KICE_DEPTH_NOTES[code]
                    kice.append({"example_questions": [n], "standard": code, "reason": why})
        rows.append({
            "question_id": qid,
            "candidate_source": c["candidate_source"],
            "final_decision": d["decision"],
            "primary_domain": DOMAIN_OF[codes[0][4:8]] if codes else "기타",
            "curriculum_codes": codes,
            "required_content": d["content"] or (p2b[qid]["required_content"] if qid in p2b else p2a[qid]["required_content"]),
            "content_relevance_reason": d["reason"],
            "adaptation_required": d["adapt"],
            "adaptation_note": d["note"],
            "drop_reasons": d["drops"] if d["decision"] == "DROP" else [],
            "kice_depth_support": kice,
            "official_answer": p2a[qid]["official_answer"],
            "review_required": bool(d["flags"]),
            "review_reasons": d["flags"],
        })
    with open(OUT_CUR, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    cand_by = {c["question_id"]: c for c in cand}
    keep = []
    for r in rows:
        if r["final_decision"] != "KEEP":
            continue
        qid = r["question_id"]
        a = p2a[qid]
        keep.append({
            "question_id": qid, "exam_id": a["exam_id"], "question_number": a["question_number"],
            "official_answer": qs[qid]["official_answer"], "primary_domain": r["primary_domain"],
            "curriculum_codes": r["curriculum_codes"], "candidate_source": r["candidate_source"],
            "adaptation_required": r["adaptation_required"], "question_image": a["question_image"],
            "phase1_review_required": a["phase1_review_required"],
            "phase2a_review_required": a["phase2a_review_required"],
            "phase2b_review_required": cand_by[qid]["phase2b_review_required"],
            "phase2c_review_required": r["review_required"],
        })
    with open(OUT_KEEP, "w", encoding="utf-8") as f:
        for r in keep:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    cnt = collections.Counter(r["final_decision"] for r in rows)
    def split(key):
        out = collections.defaultdict(lambda: {"candidates": 0, "KEEP": 0, "DROP": 0})
        for r in rows:
            out[r[key]]["candidates"] += 1
            out[r[key]][r["final_decision"]] += 1
        return dict(out)
    ids = [r["question_id"] for r in rows]
    cand_ids = [c["question_id"] for c in cand]
    keep_ids = [k["question_id"] for k in keep]
    qa = {
        "candidate_input_ids": len(cand_ids),
        "candidate_input_unique": len(set(cand_ids)),
        "output_records": len(rows),
        "keep_plus_drop": cnt["KEEP"] + cnt["DROP"],
        "invalid_decisions": sum(1 for r in rows if r["final_decision"] not in ("KEEP", "DROP")
                                 or r["adaptation_required"] not in ADAPT),
        "duplicate_ids": len(ids) - len(set(ids)),
        "missing_candidate_ids": len(set(cand_ids) - set(ids)),
        "unknown_ids": sorted(set(dec) - set(cand_ids)),
        "keep_without_curriculum_evidence": sum(1 for r in rows if r["final_decision"] == "KEEP"
                                                and (not r["curriculum_codes"] or not set(r["curriculum_codes"]) <= std)),
        "keep_without_rationale": sum(1 for r in rows if r["final_decision"] == "KEEP" and not r["content_relevance_reason"]),
        "drop_without_reason": sum(1 for r in rows if r["final_decision"] == "DROP"
                                   and (not r["drop_reasons"] or not set(r["drop_reasons"]) <= DROP_REASONS)),
        "final_index_records": len(keep),
        "final_index_equals_keep": len(keep) == cnt["KEEP"],
        "final_index_duplicate_ids": len(keep_ids) - len(set(keep_ids)),
        "final_index_drop_contamination": sum(1 for k in keep_ids if dec[k]["decision"] != "KEEP"),
        "official_answers_modified": sum(1 for r in rows if r["official_answer"] != qs[r["question_id"]]["official_answer"]),
    }
    summary = {
        "candidates": len(rows), "KEEP": cnt["KEEP"], "DROP": cnt["DROP"],
        "review_required_total": sum(r["review_required"] for r in rows),
        "keep_review_required": sum(r["review_required"] for r in rows if r["final_decision"] == "KEEP"),
        "drop_review_required": sum(r["review_required"] for r in rows if r["final_decision"] == "DROP"),
        "by_source": split("candidate_source"),
        "by_domain": split("primary_domain"),
        "keep_by_standard": dict(collections.Counter(c for r in rows if r["final_decision"] == "KEEP"
                                                     for c in r["curriculum_codes"]).most_common()),
        "keep_adaptation": dict(collections.Counter(r["adaptation_required"] for r in rows if r["final_decision"] == "KEEP")),
        "drop_reasons": dict(collections.Counter(x for r in rows for x in r["drop_reasons"]).most_common()),
        "qa": qa,
        "inputs": {os.path.relpath(p, ROOT): sha(p) for p in (P2A, P2B, INDEX, MASTER, DECISIONS)},
    }
    dump_json(OUT_SUMMARY, summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "inputs"}, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-batch", type=int)
    ap.add_argument("--batch-size", type=int, default=46)
    a = ap.parse_args()
    if a.dump_batch is not None:
        dump_batch(a.dump_batch, a.batch_size)
    else:
        build()


if __name__ == "__main__":
    main()
