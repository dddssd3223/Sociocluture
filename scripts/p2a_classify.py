"""PHASE 2A: first-pass relevance of SOCIOCULTURE questions to 2028 통합사회 + merge of semantic review.

Reads Phase 1 outputs read-only (JSONL) and the curriculum master; writes only under data/phase2/SOCIOCULTURE/.
  1. rule pass: concept lexicon hits (통합사회 scope vs 사회·문화-only mechanisms) in stem / official-answer
     choice / other choices / passage  ->  provisional DIRECT | ADAPTABLE | LOW | NONE
  2. semantic review (data/phase2/SOCIOCULTURE/phase2a_semantic_review.tsv) overrides the rule pass
  3. final JSONL + summary + QA
Usage: python3 scripts/p2a_classify.py [--dump-batch N --batch-size 40 --filter ...]
"""
import argparse
import collections
import csv
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p1_common import ROOT, dump_json, load_json

P1_JSONL = os.path.join(ROOT, "data", "questions", "SOCIOCULTURE", "SOCIOCULTURE_questions.jsonl")
MASTER = os.path.join(ROOT, "reference", "curriculum", "integrated_social_official_scope.json")
OUT_DIR = os.path.join(ROOT, "data", "phase2", "SOCIOCULTURE")
REVIEW_TSV = os.path.join(OUT_DIR, "phase2a_semantic_review.tsv")
OUT_JSONL = os.path.join(OUT_DIR, "phase2a_relevance.jsonl")
OUT_SUMMARY = os.path.join(OUT_DIR, "phase2a_relevance_summary.json")

DOMAIN_OF = {"1-01": "기타", "1-02": "기타", "1-03": "기타", "1-04": "문화와 다양성", "1-05": "생활공간과 사회",
             "2-01": "인권보장과 헌법", "2-02": "사회정의와 불평등", "2-03": "시장경제와 지속가능발전",
             "2-04": "세계화와 평화", "2-05": "미래와 지속가능한 삶"}
SUMMARY_DOMAINS = ["문화와 다양성", "사회정의와 불평등", "생활공간과 사회", "인권보장과 헌법",
                   "시장경제와 지속가능발전", "세계화와 평화", "미래와 지속가능한 삶", "기타"]

# ---- 통합사회 official-scope concepts -> standard code (navigation aid only, never a final decision)
SCOPE = {
    "10통사1-04-02": ["문화변동", "문화전파", "직접전파", "간접전파", "자극전파", "문화접변", "문화동화", "문화병존",
                     "문화융합", "문화저항", "문화지체", "문화정체성", "전통문화", "문화의획일화", "발명", "발견",
                     "외래문화", "고유문화", "문화요소", "강제적문화접변", "자발적문화접변"],
    "10통사1-04-03": ["문화상대주의", "자문화중심주의", "문화사대주의", "극단적문화상대주의", "보편윤리", "문화이해",
                     "문화를이해", "상대주의적", "명예살인", "보편적가치", "인간의존엄"],
    "10통사1-04-04": ["다문화", "이주민", "이주노동자", "외국인근로자", "외국인노동자", "결혼이민", "결혼이주",
                     "동화주의", "용광로", "샐러드볼", "모자이크", "문화다양성", "문화적다양성", "이민자", "국제결혼",
                     "북한이탈주민", "새터민", "외국인주민"],
    "10통사1-04-01": ["문화권", "종교", "음식문화", "의식주"],
    "10통사1-05-01": ["산업화", "도시화", "이촌향도", "도시성", "개인주의", "직업의분화", "교외화", "도시문제",
                     "농촌", "과밀", "과소"],
    "10통사1-05-02": ["정보화", "정보사회", "정보격차", "디지털격차", "인터넷", "사이버", "인공지능", "4차산업",
                     "제4차", "플랫폼", "개인정보", "SNS", "누리소통망", "원격", "전자상거래", "정보통신", "빅데이터",
                     "노동시장의양극화", "과학기술", "교통", "통신"],
    "10통사1-05-03": ["지역사회", "지역개발", "거점개발", "균형개발", "지역주민", "지역격차"],
    "10통사2-01-01": ["인권", "주거권", "환경권"],
    "10통사2-01-02": ["시민참여", "사회참여", "시민단체", "시민운동", "사회운동", "시민불복종", "헌법"],
    "10통사2-01-03": ["사회적소수자", "소수자", "차별", "장애인", "노동권", "청소년노동", "근로계약", "혐오"],
    "10통사2-02-01": ["분배", "업적", "필요에따른", "능력에따른", "정의"],
    "10통사2-02-03": ["불평등", "사회계층", "계층구조", "계층양극화", "양극화", "빈곤", "절대적빈곤", "상대적빈곤",
                     "사회복지", "사회보장", "사회보험", "공공부조", "사회서비스", "기초연금", "기초생활", "국민연금",
                     "적극적우대", "적극적평등", "할당제", "성불평등", "성차별", "임금격차", "소득격차", "공간불평등",
                     "저소득", "취약계층", "사회적약자", "빈부격차", "지니계수", "소득분배", "10분위", "5분위"],
    "10통사2-03-02": ["기업의사회적책임", "윤리적소비", "소비문화", "노동자", "근로자", "노동조합", "비정규직",
                     "정규직", "고용", "실업", "일자리", "합리적선택", "시장실패"],
    "10통사2-04-01": ["세계화", "지역화", "다국적기업", "세계도시", "지구촌", "세계시민", "문화의획일화"],
    "10통사2-04-02": ["국제분쟁", "평화", "국제기구", "비정부기구", "NGO"],
    "10통사2-05-01": ["저출산", "저출생", "고령화", "고령사회", "노년부양비", "유소년부양비", "총부양비", "노령화지수",
                     "부양비", "인구구조", "인구피라미드", "인구이동", "출산율", "인구문제", "노인인구", "인구과잉",
                     "생산가능인구", "부양인구", "노년인구"],
    "10통사2-05-02": ["기후변화", "에너지", "환경문제", "지속가능"],
    "10통사2-05-03": ["미래사회", "미래"],
}
# ---- 사회·문화 선택과목 고유 mechanisms (signal only; not an automatic DROP list)
SOC_ONLY = {
    "research_method": ["양적연구", "질적연구", "실증적연구", "해석적연구", "가설", "변수", "조작적정의", "표본",
                        "질문지법", "면접법", "참여관찰", "실험법", "문헌연구", "연구윤리", "가치중립", "연구절차",
                        "자료수집방법", "독립변수", "종속변수", "실험집단", "통제집단", "신뢰도", "타당도", "연구방법",
                        "연구대상", "연구주제", "모집단", "설문조사", "심층면접", "연구자", "가치개입"],
    "sociology_perspective": ["기능론", "갈등론", "상징적상호작용", "교환이론", "사회실재론", "사회명목론", "거시적",
                              "미시적"],
    "social_vs_natural": ["자연현상", "사회·문화현상", "사회문화현상", "존재법칙", "당위법칙", "가치함축", "몰가치",
                          "확률의원리", "개연성", "보편성과특수성"],
    "socialization_status": ["사회화", "재사회화", "탈사회화", "예기사회화", "사회화기관", "귀속지위", "성취지위",
                             "역할갈등", "역할긴장", "역할행동", "역할기대", "지위", "보상", "제재"],
    "groups_orgs": ["1차집단", "2차집단", "공동사회", "이익사회", "내집단", "외집단", "준거집단", "소속집단",
                    "공식조직", "비공식조직", "자발적결사체", "관료제", "탈관료제", "팀제", "네트워크조직", "사회집단",
                    "사회조직", "친목집단", "이익집단"],
    "deviance": ["일탈", "아노미", "차별교제", "낙인", "머튼", "뒤르켐", "사회통제", "1차적일탈", "2차적일탈"],
    "mobility_theory": ["세대간이동", "세대내이동", "수직이동", "수평이동", "구조적이동", "개인적이동", "사회이동",
                        "계급", "계층론", "계급론", "폐쇄적계층", "개방적계층", "계층의대물림"],
    "culture_theory": ["문화의속성", "공유성", "학습성", "축적성", "변동성", "전체성", "총체적", "총체론", "비교론",
                       "상대론적", "하위문화", "반문화", "주류문화", "대중문화", "대중매체", "문화의의미"],
    "social_change_theory": ["진화론", "순환론", "사회변동이론", "근대화론", "종속이론"],
}


def norm(t):
    return re.sub(r"\s+", "", t or "").replace("ㆍ", "·").replace("⋅", "·").replace("･", "·").replace("․", "·")


def hits(text, lex):
    t = norm(text)
    out = {}
    for key, terms in lex.items():
        h = [w for w in terms if w in t]
        if h:
            out[key] = h
    return out


def parts(q):
    choices = q.get("choices") or []
    ans = q.get("official_answer")
    ans_txt = next((c["text"] for c in choices if c["number"] == ans), "")
    other = " ".join(c["text"] for c in choices if c["number"] != ans)
    body = " ".join([q.get("shared_passage_text") or "", q.get("passage") or "", q.get("embedded_image_ocr_text") or ""])
    stem = q.get("stem") or ""
    if not stem and not choices:              # OCR questions: only the full text is available
        stem = (q.get("question_text") or "")[:160]
        body = (q.get("question_text") or "")[160:] + " " + body
    return {"stem": stem, "answer": ans_txt, "choices": other, "body": body}


def score(q):
    p = parts(q)
    w = {"stem": 3, "answer": 2, "choices": 1, "body": 1}
    sc_i, sc_s = collections.Counter(), collections.Counter()
    ev_i, ev_s = collections.defaultdict(set), collections.defaultdict(set)
    for k, t in p.items():
        for code, hs in hits(t, SCOPE).items():
            sc_i[code] += w[k] * len(hs)
            ev_i[code].update(hs)
        for cat, hs in hits(t, SOC_ONLY).items():
            sc_s[cat] += w[k] * len(hs)
            ev_s[cat].update(hs)
    stem_s = hits(p["stem"], SOC_ONLY)
    stem_i = hits(p["stem"], SCOPE)
    I, S = sum(sc_i.values()), sum(sc_s.values())
    if I == 0:
        dec = "NONE"
    elif stem_i and not stem_s and I >= 2 * S:
        dec = "DIRECT"
    elif I >= S:
        dec = "ADAPTABLE"
    else:
        dec = "LOW"
    codes = [c for c, _ in sc_i.most_common(3)]
    return {"rule_decision": dec, "I": I, "S": S, "codes": codes,
            "scope_hits": {c: sorted(v) for c, v in ev_i.items()},
            "soc_only_hits": {c: sorted(v) for c, v in ev_s.items()},
            "stem_soc_only": sorted({x for v in stem_s.values() for x in v})}


def load_questions():
    with open(P1_JSONL, encoding="utf-8") as f:
        return [json.loads(l) for l in f]


def read_review():
    rows = {}
    if not os.path.exists(REVIEW_TSV):
        return rows
    with open(REVIEW_TSV, encoding="utf-8") as f:
        for r in csv.reader((l for l in f if l.strip() and not l.startswith("#")), delimiter="\t"):
            qid, dec, codes, content, note = (r + [""] * 5)[:5]
            flags = r[5] if len(r) > 5 else ""
            qid = qid.strip()
            if not qid.startswith("SOCIOCULTURE_"):
                qid = "SOCIOCULTURE_" + qid
            rows[qid] = {"decision": dec.strip(), "codes": [c for c in codes.split(",") if c.strip()],
                         "content": [c.strip() for c in content.split(";") if c.strip()], "note": note.strip(),
                         "flags": [x for x in flags.split(",") if x.strip()]}
    return rows


def dump_batch(qs, rules, idx, size, flt):
    sel = [q for q in qs if eval(flt, {}, {"q": q, "r": rules[q["question_id"]]})] if flt else qs
    batch = sel[idx * size:(idx + 1) * size]
    print(f"# batch {idx} ({len(batch)} of {len(sel)})")
    for q in batch:
        r = rules[q["question_id"]]
        p = parts(q)
        ch = " / ".join(f"{c['number']}{'*' if c['number'] == q['official_answer'] else ''}{norm(c['text'])[:48]}"
                        for c in q["choices"]) or ("ANS=" + str(q["official_answer"]) + " " + norm(q["question_text"])[150:450])
        print(f"{q['question_id'][13:]} [{r['rule_decision'][:3]} I{r['I']}S{r['S']}]\n S:{norm(p['stem'])[:130]}"
              f"\n B:{norm(p['body'])[:200]}\n C:{ch}")


def build(qs, rules, review, master):
    std = {s["standard_code"]: s for s in master["standards"]}
    out = []
    for q in qs:
        qid = q["question_id"]
        r = rules[qid]
        rv = review.get(qid)
        dec = rv["decision"] if rv else r["rule_decision"]
        codes = (rv["codes"] if rv and rv["codes"] else r["codes"]) if dec != "NONE" else (rv["codes"] if rv else [])
        codes = [c if c.startswith("10통사") else "10통사" + c for c in codes]
        domain = DOMAIN_OF[codes[0][4:8]] if codes else "기타"
        content = rv["content"] if rv and rv["content"] else sorted({h for v in r["scope_hits"].values() for h in v})[:6]
        ev_parts = []
        if codes:
            ev_parts.append("; ".join(f"[{c}] {std[c]['standard_text']}" for c in codes if c in std))
        if r["scope_hits"]:
            ev_parts.append("scope terms: " + ", ".join(sorted({h for v in r["scope_hits"].values() for h in v})[:10]))
        if r["soc_only_hits"]:
            ev_parts.append("사문-only terms: " + ", ".join(sorted({h for v in r["soc_only_hits"].values() for h in v})[:10]))
        flags = list(rv["flags"]) if rv else []
        if not rv and dec in ("DIRECT", "ADAPTABLE"):
            flags.append("RULE_ONLY_NOT_SEMANTICALLY_REVIEWED")
        if q["review_required"] and q["text_source"] == "tesseract_ocr" and not rv:
            flags.append("OCR_TEXT_ONLY_JUDGEMENT")
        out.append({
            "question_id": qid, "exam_id": q["exam_id"], "question_number": q["question_number"],
            "phase2a_decision": dec,
            "primary_domain": domain,
            "curriculum_codes": codes,
            "required_content": content,
            "selection_evidence": " || ".join(ev_parts),
            "adaptation_note": rv["note"] if rv else "",
            "decision_source": "semantic_review" if rv else "rule_first_pass",
            "rule_first_pass": {k: r[k] for k in ("rule_decision", "I", "S", "codes", "soc_only_hits")},
            "official_answer": q["official_answer"],
            "question_image": q["question_image"],
            "phase1_review_required": q["review_required"],
            "phase1_review_reasons": q["review_reasons"],
            "phase2a_review_required": bool(flags),
            "phase2a_review_reasons": sorted(set(flags)),
            "review_required": bool(flags),
            "review_reasons": sorted(set(flags)),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-batch", type=int)
    ap.add_argument("--batch-size", type=int, default=40)
    ap.add_argument("--filter", default="")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    qs = load_questions()
    master = load_json(MASTER)
    rules = {q["question_id"]: score(q) for q in qs}
    if a.stats:
        print(collections.Counter(r["rule_decision"] for r in rules.values()))
        return
    if a.dump_batch is not None:
        dump_batch(qs, rules, a.dump_batch, a.batch_size, a.filter)
        return
    review = read_review()
    unknown = sorted(set(review) - {q["question_id"] for q in qs})
    recs = build(qs, rules, review, master)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    ids = [r["question_id"] for r in recs]
    dec = collections.Counter(r["phase2a_decision"] for r in recs)
    dom = {d: collections.Counter(r["phase2a_decision"] for r in recs if r["primary_domain"] == d) for d in SUMMARY_DOMAINS}
    qa = {
        "input_questions": len(qs), "output_records": len(recs),
        "decision_sum_ok": sum(dec.values()) == len(qs),
        "unique_question_ids": len(set(ids)), "duplicate_ids": len(ids) - len(set(ids)),
        "missing_ids": len({q["question_id"] for q in qs} - set(ids)),
        "unknown_ids_in_review": unknown,
        "direct_without_curriculum_evidence": sum(1 for r in recs if r["phase2a_decision"] == "DIRECT" and not r["curriculum_codes"]),
        "adaptable_without_evidence_or_rationale": sum(1 for r in recs if r["phase2a_decision"] == "ADAPTABLE"
                                                       and not r["curriculum_codes"] and not r["adaptation_note"]),
        "official_answers_changed": sum(1 for q, r in zip(qs, recs) if q["official_answer"] != r["official_answer"]),
        "invalid_decisions": sum(1 for r in recs if r["phase2a_decision"] not in ("DIRECT", "ADAPTABLE", "LOW", "NONE")),
        "semantic_review_rows": len(review),
    }
    summary = {
        "total": len(recs),
        "DIRECT": dec["DIRECT"], "ADAPTABLE": dec["ADAPTABLE"], "LOW": dec["LOW"], "NONE": dec["NONE"],
        "phase2a_review_required": sum(1 for r in recs if r["phase2a_review_required"]),
        "phase2a_review_reasons": dict(collections.Counter(x for r in recs for x in r["phase2a_review_reasons"])),
        "phase1_review_required_by_decision": dict(collections.Counter(r["phase2a_decision"] for r in recs if r["phase1_review_required"])),
        "decision_source": dict(collections.Counter(r["decision_source"] for r in recs)),
        "by_domain": {d: {"total": sum(c.values()), **{k: c[k] for k in ("DIRECT", "ADAPTABLE", "LOW", "NONE")}}
                      for d, c in dom.items()},
        "by_standard_direct_adaptable": dict(sorted(collections.Counter(
            c for r in recs if r["phase2a_decision"] in ("DIRECT", "ADAPTABLE") for c in r["curriculum_codes"][:1]).items())),
        "rule_first_pass": dict(collections.Counter(r["rule_first_pass"]["rule_decision"] for r in recs)),
        "qa": qa,
        "inputs": {"phase1_jsonl_sha256": hashlib.sha256(open(P1_JSONL, "rb").read()).hexdigest(),
                   "curriculum_master": os.path.relpath(MASTER, ROOT)},
    }
    dump_json(OUT_SUMMARY, summary)
    print(json.dumps({k: summary[k] for k in ("DIRECT", "ADAPTABLE", "LOW", "NONE", "phase2a_review_required",
                                              "decision_source", "qa")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
