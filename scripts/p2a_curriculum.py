"""PHASE 2A step 1: build the 통합사회1·2 official-scope master from the repository's reference PDFs.

SOURCE A  「(2022개정) 초·중등학교 교육과정 [별책7] 사회과」 PDF.pdf  -> standards, 해설, 고려 사항, 내용 요소
SOURCE B  [별첨] 2028학년도 대학수학능력시험 예시문항 안내.pdf       -> example-question ↔ standard map
Text is copied from the PDFs; nothing is paraphrased or extended.
"""
import os
import re
import sys

import pymupdf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p1_common import ROOT, dump_json, nfc, repo_files, sha256

OUT = os.path.join(ROOT, "reference", "curriculum", "integrated_social_official_scope.json")
CODE_RE = re.compile(r"\[(10통사[12]-0\d-0\d)\]")

DOMAINS = {
    "10통사1-01": ("통합사회1", "통합적 관점"),
    "10통사1-02": ("통합사회1", "인간, 사회, 환경과 행복"),
    "10통사1-03": ("통합사회1", "자연환경과 인간"),
    "10통사1-04": ("통합사회1", "문화와 다양성"),
    "10통사1-05": ("통합사회1", "생활공간과 사회"),
    "10통사2-01": ("통합사회2", "인권보장과 헌법"),
    "10통사2-02": ("통합사회2", "사회정의와 불평등"),
    "10통사2-03": ("통합사회2", "시장경제와 지속가능발전"),
    "10통사2-04": ("통합사회2", "세계화와 평화"),
    "10통사2-05": ("통합사회2", "미래와 지속가능한 삶"),
}
# 내용 체계 '지식·이해' 내용 요소, verbatim (SOURCE A p.108, p.115)
KNOWLEDGE = {
    "통합적 관점": ["통합적 관점", "시간적 관점", "공간적 관점", "사회적 관점", "윤리적 관점"],
    "인간, 사회, 환경과 행복": ["행복의 의미", "행복의 조건"],
    "자연환경과 인간": ["자연환경", "자연관", "환경문제", "생태시민"],
    "문화와 다양성": ["문화권", "문화 변동", "문화 상대주의와 보편윤리", "다문화 사회"],
    "생활공간과 사회": ["산업화와 도시화", "교통⋅통신과 과학기술의 발달", "생활공간과 생활양식", "지역사회"],
    "인권보장과 헌법": ["시민혁명", "인권", "헌법", "시민참여"],
    "사회정의와 불평등": ["정의의 실질적 기준", "정의관", "사회불평등", "공간불평등"],
    "시장경제와 지속가능발전": ["시장경제와 합리적 선택", "경제 주체의 역할", "국제 분업과 무역", "금융 생활"],
    "세계화와 평화": ["세계화", "국제분쟁", "평화", "세계시민"],
    "미래와 지속가능한 삶": ["인구 문제", "자원 위기", "미래 삶의 방향", "지속가능발전"],
}
PAGE_FOOTER = re.compile(r"^(사회과 교육과정|선택 중심 교육과정 – 공통 과목 -|\d{3})$")


def pdf_by_key(key):
    files = repo_files()
    name = [k for k in files if key in k][0]
    return name, os.path.join(ROOT, files[name])


def clean_lines(doc, first=0, last=None):
    lines = []
    for i, p in enumerate(doc):
        if i < first or (last is not None and i > last):
            continue
        for ln in nfc(p.get_text()).splitlines():
            s = ln.strip()
            if s and not PAGE_FOOTER.match(s):
                # a real space at a line break is kept by the PDF as a trailing space
                lines.append((i + 1, s + (" " if ln.endswith(" ") else "")))
    return lines


def main():
    a_name, a_path = pdf_by_key("별책7")
    b_name, b_path = pdf_by_key("예시문항")
    doc = pymupdf.open(a_path)
    lines = clean_lines(doc)
    # join continuation lines into paragraphs: standards start with [code], bullets with •
    paras, cur = [], None
    for page, s in lines:
        starts = CODE_RE.match(s) or s.startswith("•") or re.match(r"^\((가|나)\)|^\(\d\) |^\[통합사회", s) \
            or s.startswith("⋅")
        if starts or cur is None:
            if cur:
                paras.append(cur)
            cur = {"page": page, "text": s}
        else:
            cur["text"] += s
    paras.append(cur)
    # keep Korean line-wrap joins tight: PDF wraps mid-word, so remove the space we inserted between Hangul
    for p in paras:
        p["text"] = re.sub(r"\s+", " ", p["text"]).strip()

    standards = {}
    section, unit = None, None
    unit_notes = {}
    for p in paras:
        t = p["text"]
        if t.startswith("(가) 성취기준 해설"):
            section = "exp"
            continue
        if t.startswith("(나) 성취기준 적용 시 고려 사항"):
            section = "cons"
            continue
        m = CODE_RE.match(t)
        if m and section != "exp":
            code = m.group(1)
            unit = code[:8]
            section = "std"
            standards[code] = {"standard_code": code, "standard_text": t[m.end():].strip(), "page": p["page"],
                               "explanation": "", "application_considerations": []}
            continue
        if t.startswith("•") and section == "exp":
            mm = CODE_RE.search(t)
            if mm and mm.group(1) in standards:
                standards[mm.group(1)]["explanation"] = t.lstrip("• ").strip()
            continue
        if t.startswith("•") and section == "cons" and unit:
            unit_notes.setdefault(unit, []).append(t.lstrip("• ").strip())
            continue
        if re.match(r"^\(\d\) ", t) or t.startswith("[통합사회"):
            section = None

    # SOURCE B: example-question curriculum basis table (부록)
    bdoc = pymupdf.open(b_path)
    btxt = nfc("\n".join(p.get_text() for p in bdoc))
    appendix = btxt[btxt.find("<사회탐구> 영역   (통합사회) 과목"):]
    appendix = appendix[:appendix.find("<사회탐구> 영역", 10)] if appendix.find("<사회탐구> 영역", 10) > 0 else appendix
    ex_map = {}
    qn = None
    for ln in appendix.splitlines():
        s = ln.strip()
        if re.fullmatch(r"\d{1,2}", s) and 1 <= int(s) <= 25:
            qn = int(s)
            continue
        for c in CODE_RE.findall(s):
            if qn:
                ex_map.setdefault(qn, [])
                if c not in ex_map[qn]:
                    ex_map[qn].append(c)

    out_std = []
    for code in sorted(standards):
        s = standards[code]
        subj, dom = DOMAINS[code[:8]]
        s.update({"subject": subj, "domain": dom, "knowledge_elements": KNOWLEDGE[dom],
                  "application_considerations": unit_notes.get(code[:8], []),
                  "example_questions_2028": sorted(q for q, cs in ex_map.items() if code in cs)})
        out_std.append(s)
    master = {
        "title": "2022 개정 통합사회1·2 공식 범위 MASTER (PHASE 2A)",
        "sources": {
            "A": {"file": a_name, "sha256": sha256(a_path), "role": "primary scope (내용 체계, 성취기준, 해설, 고려 사항)"},
            "B": {"file": b_name, "sha256": sha256(b_path), "role": "operational reference (예시문항 교육과정 근거)"},
            "C": {"file": "[2022 개정 교육과정에 따른 최소 성취수준 보장 지도 자료] 05.통합사회1,2.pdf",
                  "role": "auxiliary; not needed for scope decisions, not parsed"},
        },
        "domains": [{"subject": v[0], "domain": v[1], "code_prefix": k, "knowledge_elements": KNOWLEDGE[v[1]]}
                    for k, v in DOMAINS.items()],
        "standards": out_std,
        "example_question_map_2028": {str(k): v for k, v in sorted(ex_map.items())},
    }
    dump_json(OUT, master)
    print(len(out_std), "standards;", sum(1 for s in out_std if s["explanation"]), "with 해설;",
          len(ex_map), "example questions mapped")
    for s in out_std:
        if not s["explanation"] or not s["application_considerations"]:
            print("MISSING", s["standard_code"])


if __name__ == "__main__":
    main()
