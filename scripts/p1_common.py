"""Shared helpers for SOCIOCULTURE PHASE 1 (raw past-exam DB)."""
import hashlib
import json
import os
import re
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBJECT = "SOCIOCULTURE"
PIPELINE_VERSION = "p1-v1"

DATA = os.path.join(ROOT, "data")
Q_DIR = os.path.join(DATA, "questions", SUBJECT)
Q_JSON_DIR = os.path.join(Q_DIR, "json")
Q_IMG_DIR = os.path.join(Q_DIR, "images")
ANS_DIR = os.path.join(DATA, "answers", SUBJECT)
REPORT_DIR = os.path.join(DATA, "reports", SUBJECT)
CACHE_DIR = os.path.join(DATA, "cache", SUBJECT)
DB_PATH = os.path.join(DATA, "database", "questions.db")

EXAM_TYPE = {"06월": "JUNE", "09월": "SEPTEMBER", "수능": "CSAT"}
EXAM_PERIOD_LABEL = {"JUNE": "6월 모의평가", "SEPTEMBER": "9월 모의평가", "CSAT": "대학수학능력시험"}
FNAME_RE = re.compile(r"^(\d{4})학년도_(06월|09월|수능)_(사회문화|사탐전체)_(문제|해설|정답표?)\.(pdf|jpg)$")
CIRCLED = "①②③④⑤"


def nfc(s):
    return unicodedata.normalize("NFC", s)


def repo_files():
    """Map NFC-normalised relative name -> actual on-disk name (top level only)."""
    return {nfc(x): x for x in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, x))}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def exam_id(year, exam_type):
    return f"{SUBJECT}_{year}_{exam_type}"


def question_id(year, exam_type, qnum):
    return f"{SUBJECT}_{year}_{exam_type}_Q{qnum:02d}"


def parse_fname(name):
    m = FNAME_RE.match(nfc(name))
    if not m:
        return None
    return {"year": int(m.group(1)), "exam_type": EXAM_TYPE[m.group(2)], "scope": m.group(3),
            "kind": m.group(4), "ext": m.group(5)}


def dump_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)
