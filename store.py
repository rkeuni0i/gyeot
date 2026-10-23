"""근거 자료 저장소(SQLite, index/gyeot.db).

한 파일 안에 문서 정보·주제·질문 유형·청크(본문·형태소·임베딩)·평가 기록·알림을 함께 둔다.
- 문서를 지우면 외래키 CASCADE로 그 문서의 청크·주제·평가 기록·알림이 한 번에 지워진다(트랜잭션).
- 벡터 검색은 실행 중 메모리(numpy)에서 한다. 청크가 수만 개를 넘거나 여러 사용자가 동시에 쓰는 서버로 옮길 때는
  같은 스키마를 PostgreSQL + pgvector로 옮기면 된다.

층(layer)
- common: 모든 질문에서 볼 수 있는 공통 자료(법령, 기관 연락처, Wee 운영 가이드 등)
- topic : 질문 주제가 겹칠 때만 보는 주제 자료(학교폭력, 가정, 학업·학교부적응, 정서·위기, 디지털·사이버, 상담 기법)
- school: 특정 학교 자료(학칙 등). 법령·지침보다 하위로 다룬다.
"""
import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "index" / "gyeot.db"  # 2026-10-19 mindguide.db에서 이름 변경
SCHEMA_VERSION = "2"  # 2: documents.region(적용 지역)·ocr_engine(OCR로 읽은 자료) 추가
TOPICS = ["학교폭력", "가정", "학업·학교부적응", "정서·위기", "디지털·사이버", "상담 기법", "학교 운영"]
LAYERS = ["common", "topic", "school"]
# 적용 지역: 전국 공통 → 시·도 → 시·군(교육지원청 관할) → 이 학교. 학교 지역과 다르면 답변에서 경고한다
REGIONS = ["전국", "경기", "화성·오산", "학교"]
REVIEW_INTERVAL_DAYS = 182  # 한 학기

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS schools (
  school_id TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT, info TEXT);
CREATE TABLE IF NOT EXISTS documents (
  doc_id TEXT PRIMARY KEY,
  short TEXT NOT NULL UNIQUE,                  -- 답변 인용 이름 [short p.N]
  title TEXT NOT NULL,
  file_name TEXT NOT NULL,                     -- data/ 아래 PDF 파일 이름
  file_sha256 TEXT,                            -- 같은 파일 중복 등록 방지
  layer TEXT NOT NULL CHECK (layer IN ('common','topic','school')),
  kind TEXT NOT NULL,                          -- guide / school / manual / reference
  school_id TEXT REFERENCES schools(school_id),
  publisher TEXT, published_year INTEGER,
  region TEXT,                                 -- 적용 지역(REGIONS 중 하나 또는 다른 시·도 이름)
  ocr_engine TEXT,                             -- 글자층이 없어 OCR로 읽었으면 그 엔진(gemini/windows)
  is_base INTEGER NOT NULL DEFAULT 0,          -- 처음부터 있던 기본 자료(완전 삭제는 인용 이름 입력 확인 필요, 빈 자리로 다시 등록 가능)
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
  toc_json TEXT, page_offset INTEGER,          -- 매뉴얼 목차(인쇄 쪽, 장 이름)와 쪽 차이
  summary TEXT, note TEXT,
  added_at TEXT, added_by TEXT,
  last_reviewed_at TEXT, next_review_due TEXT);
CREATE TABLE IF NOT EXISTS doc_topics (
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  topic TEXT NOT NULL, PRIMARY KEY (doc_id, topic));
CREATE TABLE IF NOT EXISTS doc_qtypes (
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  qtype TEXT NOT NULL, PRIMARY KEY (doc_id, qtype));
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  page INTEGER NOT NULL, section TEXT, text TEXT NOT NULL,
  tokens_json TEXT NOT NULL, embedding BLOB NOT NULL, embed_model TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS reviews (
  review_id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  reviewed_at TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('intake','periodic','manual')),
  verdict TEXT, weighted_score REAL, report_json TEXT);
CREATE TABLE IF NOT EXISTS alerts (
  alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  review_id INTEGER REFERENCES reviews(review_id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('info','warn','critical')),
  title TEXT NOT NULL, message TEXT,
  acknowledged_at TEXT, acknowledged_by TEXT);
CREATE INDEX IF NOT EXISTS idx_alerts_open ON alerts(doc_id, acknowledged_at);
"""


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


class StoreMissing(FileNotFoundError):
    """저장소 파일이 없다(이름이 바뀌었거나 옮겨졌을 수 있음). 빈 저장소를 몰래 새로 만들지 않는다."""


def connect(path=DB_PATH, create=False):
    """create=False(기본)면 저장소 파일이 없을 때 빈 파일을 만들지 않고 StoreMissing을 낸다.
    빈 저장소가 생기면 화면은 '자료 0개'로 보이고 검색·평가가 깨지므로, 새로 만드는 것은 build_index.py만 한다."""
    if not Path(path).exists() and not create:
        raise StoreMissing(f"자료 저장소 {Path(path).relative_to(ROOT) if Path(path).is_relative_to(ROOT) else path}가 없습니다. "
                           "파일 이름을 바꿨거나 옮겼다면 되돌리고, 처음 설치라면 build_index.py를 실행하세요.")
    path.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    ensure_schema(con)
    return con


def ensure_schema(con):
    """이전 판 저장소에 없는 칸을 더한다(데이터는 그대로). 표가 아직 없으면(새 저장소) init_db가 만든다."""
    cols = {r[1] for r in con.execute("PRAGMA table_info(documents)")}
    if not cols:
        return
    for col in ("region", "ocr_engine"):
        if col not in cols:
            con.execute(f"ALTER TABLE documents ADD COLUMN {col} TEXT")
    if get_meta(con, "schema_version") != SCHEMA_VERSION:
        set_meta(con, "schema_version", SCHEMA_VERSION)
    con.commit()


def init_db(con):
    con.executescript(SCHEMA)
    ensure_schema(con)
    con.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
    con.execute("INSERT OR IGNORE INTO meta VALUES ('review_interval_days', ?)", (str(REVIEW_INTERVAL_DAYS),))
    con.commit()


def get_meta(con, key, default=None):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(con, key, value):
    con.execute("INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


# ---------- 문서 ----------
def upsert_document(con, doc_id, **f):
    cols = ["short", "title", "file_name", "file_sha256", "layer", "kind", "school_id", "publisher", "published_year",
            "region", "ocr_engine",
            "is_base", "status", "toc_json", "page_offset", "summary", "note", "added_at", "added_by",
            "last_reviewed_at", "next_review_due"]
    vals = {c: f.get(c) for c in cols if c in f}
    if con.execute("SELECT 1 FROM documents WHERE doc_id=?", (doc_id,)).fetchone():
        if vals:
            con.execute(f"UPDATE documents SET {', '.join(f'{c}=?' for c in vals)} WHERE doc_id=?", (*vals.values(), doc_id))
    else:
        con.execute(f"INSERT INTO documents (doc_id, {', '.join(vals)}) VALUES (?, {', '.join('?' * len(vals))})",
                    (doc_id, *vals.values()))


def set_topics(con, doc_id, topics):
    con.execute("DELETE FROM doc_topics WHERE doc_id=?", (doc_id,))
    con.executemany("INSERT OR IGNORE INTO doc_topics VALUES (?, ?)", [(doc_id, t) for t in topics])


def set_qtypes(con, doc_id, qtypes):
    con.execute("DELETE FROM doc_qtypes WHERE doc_id=?", (doc_id,))
    con.executemany("INSERT OR IGNORE INTO doc_qtypes VALUES (?, ?)", [(doc_id, q) for q in qtypes])


def documents(con, status=None):
    """{doc_id: dict}. topics·qtypes·toc 포함."""
    q = "SELECT * FROM documents" + (" WHERE status=?" if status else "") + " ORDER BY is_base DESC, added_at"
    out = {}
    for r in con.execute(q, (status,) if status else ()):
        d = dict(r)
        d["topics"] = [x["topic"] for x in con.execute("SELECT topic FROM doc_topics WHERE doc_id=?", (r["doc_id"],))]
        d["qtypes"] = [x["qtype"] for x in con.execute("SELECT qtype FROM doc_qtypes WHERE doc_id=?", (r["doc_id"],))]
        d["toc"] = [tuple(x) for x in json.loads(r["toc_json"])] if r["toc_json"] else None
        out[r["doc_id"]] = d
    return out


def delete_document(con, doc_id):
    """문서 행 삭제 → 청크·주제·질문 유형·평가 기록·알림이 CASCADE로 함께 지워진다."""
    con.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))


# ---------- 청크 ----------
def replace_chunks(con, doc_id, chunks, vecs, embed_model):
    import numpy as np
    con.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    con.executemany(
        "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(c["id"], doc_id, c["page"], c["section"], c["text"], json.dumps(c["tokens"], ensure_ascii=False),
          np.asarray(v, dtype="float32").tobytes(), embed_model) for c, v in zip(chunks, vecs)])


def load_chunks(con, doc_ids):
    """활성 문서의 청크와 임베딩 행렬을 읽는다."""
    import numpy as np
    if not doc_ids:
        return [], None
    marks = ",".join("?" * len(doc_ids))
    rows = con.execute(f"SELECT chunk_id, doc_id, page, section, text, tokens_json, embedding FROM chunks "
                       f"WHERE doc_id IN ({marks}) ORDER BY doc_id, page, chunk_id", tuple(doc_ids)).fetchall()
    chunks = [{"id": r["chunk_id"], "doc": r["doc_id"], "page": r["page"], "section": r["section"] or "",
               "text": r["text"], "tokens": json.loads(r["tokens_json"])} for r in rows]
    emb = np.vstack([np.frombuffer(r["embedding"], dtype="float32") for r in rows]) if rows else None
    return chunks, emb


def chunk_counts(con):
    return {r["doc_id"]: r["n"] for r in con.execute("SELECT doc_id, COUNT(*) n FROM chunks GROUP BY doc_id")}


# ---------- 평가 기록·알림 ----------
def add_review(con, doc_id, kind, report):
    cur = con.execute("INSERT INTO reviews (doc_id, reviewed_at, kind, verdict, weighted_score, report_json) "
                      "VALUES (?, ?, ?, ?, ?, ?)",
                      (doc_id, report.get("evaluated_at", now()), kind, report.get("verdict"),
                       report.get("weighted_score"), json.dumps(report, ensure_ascii=False)))
    return cur.lastrowid


def reviews(con, doc_id):
    return [dict(r) for r in con.execute("SELECT review_id, reviewed_at, kind, verdict, weighted_score FROM reviews "
                                         "WHERE doc_id=? ORDER BY reviewed_at DESC", (doc_id,))]


def last_review(con, doc_id):
    r = con.execute("SELECT * FROM reviews WHERE doc_id=? ORDER BY reviewed_at DESC LIMIT 1", (doc_id,)).fetchone()
    return dict(r) if r else None


def add_alert(con, doc_id, severity, title, message, review_id=None):
    con.execute("INSERT INTO alerts (doc_id, review_id, created_at, severity, title, message) VALUES (?, ?, ?, ?, ?, ?)",
                (doc_id, review_id, now(), severity, title, message))


def open_alerts(con, doc_id=None):
    q = ("SELECT a.*, d.short, d.title AS doc_title FROM alerts a JOIN documents d USING(doc_id) "
         "WHERE a.acknowledged_at IS NULL" + (" AND a.doc_id=?" if doc_id else "") +
         " ORDER BY CASE a.severity WHEN 'critical' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, a.created_at DESC")
    return [dict(r) for r in con.execute(q, (doc_id,) if doc_id else ())]


def acknowledge_alert(con, alert_id, who="상담사"):
    con.execute("UPDATE alerts SET acknowledged_at=?, acknowledged_by=? WHERE alert_id=?", (now(), who, alert_id))
