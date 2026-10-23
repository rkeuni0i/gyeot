"""평가를 마친 PDF를 저장소(store.py, SQLite)에 등록·사용 중지·완전 삭제한다.

등록      : PDF를 data/로 복사 → documents·주제·질문 유형 기록 → 그 자료 청크만 임베딩해 chunks에 저장
            → 입고 평가(reviews, kind=intake)와 주의할 점(alerts) 기록. 한 트랜잭션으로 처리
사용 중지 : status=inactive. 청크는 남겨 두고 검색에서만 뺀다(기본 자료도 가능, 되돌리기 가능)
완전 삭제 : 문서 행 삭제(청크·평가 기록·알림 CASCADE) + data/·doc_agent/inbox/의 PDF 파일 삭제. 기본 자료는 불가

화면(add_page.py)과 정기 점검(doc_agent/periodic_review.py)이 쓴다.
"""
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
ROOT = AGENT_DIR.parent
sys.path.insert(0, str(ROOT))

import rag  # noqa: E402
import store  # noqa: E402
from doc_agent import dedupe  # noqa: E402

LAYER_BY_ROLE = {"procedure": "topic", "manual": "topic", "reference": "topic", "school": "school"}
KIND_BY_ROLE = {"procedure": "guide", "manual": "manual", "reference": "reference", "school": "school"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def find_same_file(path):
    """같은 내용의 PDF가 이미 등록돼 있으면 그 doc_id."""
    digest = sha256(path)
    con = store.connect()
    row = con.execute("SELECT doc_id FROM documents WHERE file_sha256=?", (digest,)).fetchone()
    con.close()
    return row["doc_id"] if row else None


def _doc_id(con, short):
    base = "add_" + re.sub(r"[^0-9A-Za-z가-힣]", "", short)[:12]
    doc_id, n = base, 2
    while con.execute("SELECT 1 FROM documents WHERE doc_id=?", (doc_id,)).fetchone():
        doc_id, n = f"{base}{n}", n + 1
    return doc_id


def caution_alerts(con, doc_id, report, review_id):
    """평가의 주의할 점·낮은 점수를 '사용 전 알림'으로 남긴다."""
    for c in report["proposal"].get("cautions", []):
        store.add_alert(con, doc_id, "info", "사용 시 유의사항", c, review_id)
    ocr = (report.get("measured") or {}).get("ocr")
    if ocr:
        store.add_alert(con, doc_id, "info", "OCR로 읽은 자료",
                        f"글자층이 없는 PDF라 {ocr.get('engine')} OCR로 {ocr.get('pages_ocr', '?')}쪽을 읽었습니다. "
                        "숫자·표·연락처는 원본 쪽 이미지로 확인하세요.", review_id)
    for cid, s in report["scores"].items():
        if s.get("score") == 0 and s.get("weight"):
            store.add_alert(con, doc_id, "warn", f"{s['name']} 0점", s.get("evidence", ""), review_id)


def doc_year(pdf_path, agent_years, title):
    """저장할 발행 연도: PDF 글자(표지 1쪽 → 발행 정보 → 본문 표현) → 평가 에이전트가 읽은 연도 → 파일 이름 → 제목."""
    pages = dedupe.pdf_text(pdf_path)
    yi = dedupe.year_info(pages)
    if yi["year"]:
        return yi["year"]
    if agent_years:
        return max(agent_years)
    return dedupe.year_info([], Path(pdf_path).name, title)["year"]


def guess_region(report, layer=None):
    """적용 지역 추정: 학교 층이면 '학교', 평가가 제안한 지역, 표지의 시·도 교육청 이름, 그 밖에는 '전국'."""
    if layer == "school" or report["proposal"].get("role") == "school":
        return "학교"
    r = report["proposal"].get("region")
    if r in store.REGIONS or (r and r not in ("", "기타")):
        return r
    pubs = " ".join(report["measured"].get("publisher_candidates", []))
    if "교육부" not in pubs:  # 교육부가 함께 낸 자료는 전국 공통으로 본다
        m = re.search(r"(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충청북|충북|충청남|충남|전라북|전북|"
                      r"전라남|전남|경상북|경북|경상남|경남|제주)[가-힣]*교육청", pubs)
        if m:
            return m.group(1)
    return "전국"


def ocr_engine_of(pdf_path):
    c = rag.ocr_cache(pdf_path)
    return c["engine"] if c else None


def empty_base_slots():
    """완전 삭제돼 비어 있는 기본 자료 자리(doc_id 목록)."""
    con = store.connect()
    have = {r["doc_id"] for r in con.execute("SELECT doc_id FROM documents")}
    con.close()
    return [d for d in rag.BASE_SEED if d not in have]


def add_document(pdf_path, report, short=None, title=None, use_in=None, topics=None, layer=None,
                 approved_by="상담사", note="", base_slot=None, region=None, progress=None):
    """report: doc_agent.agent.evaluate_pdf 결과. 나머지는 화면에서 고친 값(없으면 에이전트 제안).
    base_slot: 비어 있는 기본 자료 자리(예: 'guide')에 등록하면 그 ID·인용 이름으로 들어가 기본 자료로 취급된다
    (질문 유형별 확인 순서, 가이드북 양식 연결, 원래 판 파일이면 장 구간표·목차까지 그대로)."""
    prop = report["proposal"]
    if base_slot:
        seed = rag.BASE_SEED[base_slot]
        short = seed["short"]
    short = (short or prop["short"]).strip()
    con = store.connect()
    store.init_db(con)
    if base_slot and con.execute("SELECT 1 FROM documents WHERE doc_id=?", (base_slot,)).fetchone():
        con.close()
        raise ValueError(f"기본 자료 자리 [{short}]에는 이미 자료가 있습니다.")
    if con.execute("SELECT 1 FROM documents WHERE short=?", (short,)).fetchone():
        con.close()
        raise ValueError(f"인용 이름 '{short}'은 이미 쓰고 있습니다.")
    digest = sha256(pdf_path)
    same = con.execute("SELECT short FROM documents WHERE file_sha256=?", (digest,)).fetchone()
    if same:
        con.close()
        raise ValueError(f"같은 PDF가 이미 [{same['short']}]로 등록돼 있습니다.")
    doc_id = base_slot or _doc_id(con, short)
    dest = ROOT / "data" / Path(pdf_path).name
    if Path(pdf_path).resolve() != dest.resolve():
        shutil.copy2(pdf_path, dest)

    role = prop.get("role", "reference")
    years = report["measured"].get("years_on_cover") or []
    year = report.get("dedupe_year") or doc_year(pdf_path, years, title or prop["title"])
    extra = {}
    if base_slot:
        seed, bmeta = rag.BASE_SEED[base_slot], rag.BASE_META[base_slot]
        same_file = dest.name == seed["file"]  # 원래 판이면 목차·쪽 보정을 그대로 쓴다
        extra = dict(kind=seed["kind"], is_base=1, school_id=bmeta.get("school_id"),
                     toc_json=json.dumps(seed["toc"]) if same_file and seed.get("toc") else None,
                     page_offset=seed.get("offset") if same_file else None)
    try:
        store.upsert_document(
            con, doc_id, short=short, title=title or prop["title"], file_name=dest.name, file_sha256=digest,
            layer=layer or LAYER_BY_ROLE.get(role, "topic"), kind=KIND_BY_ROLE.get(role, "reference"),
            school_id="esan" if role == "school" else None,
            publisher=", ".join(report["measured"].get("publisher_candidates", [])[:3]) or None,
            published_year=year, is_base=0, status="active",
            region=region or guess_region(report, layer), ocr_engine=ocr_engine_of(pdf_path),
            summary=prop.get("summary"), note=note, added_at=store.now(), added_by=approved_by,
            last_reviewed_at=report["evaluated_at"])
        if extra:
            store.upsert_document(con, doc_id, **extra)
        store.set_topics(con, doc_id, topics if topics is not None else
                         [t for t in prop.get("topics", []) if t in store.TOPICS] or _map_topics(prop.get("topics", [])))
        store.set_qtypes(con, doc_id, use_in if use_in is not None else prop["use_in"])
        review_id = store.add_review(con, doc_id, "intake", report)
        caution_alerts(con, doc_id, report, review_id)
        con.commit()  # 문서 정보 먼저 확정(청크 만들 때 DOCS가 이 문서를 알아야 함)

        rag.refresh_docs()
        chunks = rag.build_chunks(only=[doc_id])
        for c in chunks:
            c["tokens"] = rag.tokenize(c["text"])
        vecs = rag.embed_texts([rag.chunk_embed_text(c) for c in chunks], progress=progress)
        store.replace_chunks(con, doc_id, chunks, vecs, rag.EMBED_MODEL)
        con.commit()
    except Exception:
        # 임베딩 등이 실패하면 등록 자체를 되돌린다(청크 없는 문서가 남지 않게)
        con.rollback()
        store.delete_document(con, doc_id)
        con.commit()
        con.close()
        rag.refresh_docs()
        raise
    con.close()
    rag.refresh_docs()
    return doc_id, len(chunks)


def replace_edition(old_id, pdf_path, report, title=None, use_in=None, topics=None, layer=None,
                    approved_by="상담사", note="", region=None, progress=None):
    """등록된 자료(old_id)를 개정판 PDF로 교체한다. 상담사가 화면에서 확인한 뒤에만 부른다.

    유지: 자료 ID·인용 이름·층·주제·질문 유형(화면에서 고친 값이 있으면 그 값), 이전 평가 기록(이력)
    교체: PDF 파일·제목·발행 연도·청크와 임베딩. 이전 판 전용 장 구간표(목차·쪽 보정)는 지운다
    삭제: 이전 판 PDF(data/·inbox/), 이전 판 청크, 이전 판에 대해 열려 있던 알림(확인 처리)
    기본 자료도 교체할 수 있다(ID가 그대로라 질문 유형별 확인 순서가 유지된다).
    새 청크 임베딩이 끝난 뒤에 한 번에 바꾸므로, 중간에 실패하면 이전 판이 그대로 남는다.
    """
    prop = report["proposal"]
    con = store.connect()
    old = store.documents(con).get(old_id)
    if not old:
        con.close()
        raise ValueError("교체할 이전 판이 등록돼 있지 않습니다.")
    digest = sha256(pdf_path)
    same = con.execute("SELECT short FROM documents WHERE file_sha256=?", (digest,)).fetchone()
    if same:
        con.close()
        raise ValueError(f"같은 PDF가 이미 [{same['short']}]로 등록돼 있습니다.")
    dest = ROOT / "data" / Path(pdf_path).name
    if Path(pdf_path).resolve() != dest.resolve():
        shutil.copy2(pdf_path, dest)
    years = report["measured"].get("years_on_cover") or []
    new_year = report.get("dedupe_year") or doc_year(pdf_path, years, title or prop["title"])

    # 1) 새 판 청크·임베딩을 먼저 만든다(저장소는 아직 그대로)
    meta = {"file": dest.name, "kind": old["kind"], "toc": None, "offset": None}
    chunks = rag.doc_chunks(old_id, meta)
    for c in chunks:
        c["tokens"] = rag.tokenize(c["text"])
    vecs = rag.embed_texts([rag.chunk_embed_text(c) for c in chunks], progress=progress)

    # 2) 한 트랜잭션으로 교체
    old_file, old_year = old["file_name"], old["published_year"]
    try:
        store.upsert_document(
            con, old_id, title=title or prop["title"], file_name=dest.name, file_sha256=digest,
            layer=layer or old["layer"], published_year=new_year,
            region=region or old.get("region") or guess_region(report, layer or old["layer"]),
            ocr_engine=ocr_engine_of(pdf_path),
            publisher=", ".join(report["measured"].get("publisher_candidates", [])[:3]) or old["publisher"],
            toc_json=None, page_offset=None, status="active", summary=prop.get("summary"),
            note=(note + " " if note else "") + f"개정판 교체: {old_file}({old_year}) → {dest.name}({new_year})",
            last_reviewed_at=report["evaluated_at"])
        if topics is not None:
            store.set_topics(con, old_id, topics)
        if use_in is not None:
            store.set_qtypes(con, old_id, use_in)
        store.replace_chunks(con, old_id, chunks, vecs, rag.EMBED_MODEL)
        con.execute("UPDATE alerts SET acknowledged_at=?, acknowledged_by=? WHERE doc_id=? AND acknowledged_at IS NULL",
                    (store.now(), "개정판 교체(이전 판 알림)", old_id))
        review_id = store.add_review(con, old_id, "intake", report)
        store.add_alert(con, old_id, "info", "개정판으로 교체됨",
                        f"{old_year or '?'}년 판({old_file})을 {new_year or '?'}년 판({dest.name})으로 바꿨습니다. "
                        f"승인: {approved_by}. 바뀐 기한·절차가 있는지 한 번 확인하세요.", review_id)
        caution_alerts(con, old_id, report, review_id)
        con.commit()
    except Exception:
        con.rollback()
        con.close()
        if dest.name != old_file and dest.exists() and Path(pdf_path).resolve() != dest.resolve():
            dest.unlink()
        raise
    con.close()

    # 3) 이전 판 PDF 삭제(다른 자료가 같은 파일을 쓰지 않을 때만)
    removed = []
    if old_file != dest.name:
        con = store.connect()
        still_used = con.execute("SELECT 1 FROM documents WHERE file_name=?", (old_file,)).fetchone()
        con.close()
        if not still_used:
            for p in [ROOT / "data" / old_file, AGENT_DIR / "inbox" / old_file]:
                if p.exists():
                    p.unlink()
                    removed.append(str(p.relative_to(ROOT)))
    rag.refresh_docs()
    return len(chunks), removed


def _map_topics(raw):
    """에이전트가 쓴 주제 이름('정서·위기(자해·자살)' 등)을 저장소 주제 목록으로 맞춘다."""
    out = []
    for r in raw:
        for t in store.TOPICS:
            if t.split("·")[0] in r and t not in out:
                out.append(t)
    return out


def set_status(doc_id, status):
    """active ↔ inactive. 기본 자료도 사용 중지할 수 있다(청크는 남김)."""
    con = store.connect()
    store.upsert_document(con, doc_id, status=status)
    con.commit()
    con.close()
    rag.refresh_docs()


def delete_document(doc_id, delete_file=True, allow_base=False):
    """완전 삭제: 문서·청크·평가 기록·알림을 지우고 PDF 파일도 지운다.
    기본 자료는 allow_base=True(화면에서 인용 이름을 직접 입력해 확인)일 때만 지운다. 지운 기본 자료의 자리는
    비어 있게 되고, 자료 추가에서 그 자리로 다시 등록하면 질문 유형별 확인 순서·양식 연결이 되살아난다."""
    if doc_id in rag.BASE_DOCS and not allow_base:
        raise ValueError("기본 자료는 인용 이름을 입력해 확인해야 완전 삭제할 수 있습니다.")
    con = store.connect()
    row = con.execute("SELECT file_name FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
    if not row:
        con.close()
        raise ValueError("등록된 자료가 아닙니다.")
    file_name = row["file_name"]
    store.delete_document(con, doc_id)
    con.commit()
    still_used = con.execute("SELECT 1 FROM documents WHERE file_name=?", (file_name,)).fetchone()
    con.close()
    removed = []
    if delete_file and not still_used:
        for p in [ROOT / "data" / file_name, AGENT_DIR / "inbox" / file_name]:
            if p.exists():
                p.unlink()
                removed.append(str(p.relative_to(ROOT)))
    rag.refresh_docs()
    return removed


def reindex_document(doc_id):
    """한 자료의 청크를 다시 만든다(장 이름 규칙을 바꿨거나 PDF를 같은 이름으로 교체했을 때)."""
    rag.refresh_docs()
    chunks = rag.build_chunks(only=[doc_id])
    for c in chunks:
        c["tokens"] = rag.tokenize(c["text"])
    vecs = rag.embed_texts([rag.chunk_embed_text(c) for c in chunks])
    con = store.connect()
    store.replace_chunks(con, doc_id, chunks, vecs, rag.EMBED_MODEL)
    store.upsert_document(con, doc_id, file_sha256=sha256(ROOT / "data" / rag.DOCS[doc_id]["file"]))
    con.commit()
    con.close()
    return len(chunks)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    con = store.connect()
    for d, m in store.documents(con).items():
        print(d, m["short"], m["layer"], m["status"], m["topics"], m["qtypes"])
    print(json.dumps(store.chunk_counts(con), ensure_ascii=False))
