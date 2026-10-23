"""올리는 PDF가 이미 등록된 자료와 같은 자료인지, 개정판(새 판)인지, 구버전인지를 본문 내용으로 판단한다.

파일 이름은 보지 않는다. API 호출 없이 몇 초 안에 끝나므로 AI 평가 전에 바로 돌린다.
  1) 파일 바이트가 같으면(sha256) → 같은 파일
  2) 본문 겹침: 숫자·기호를 지운 단어 4개 묶음(shingle) 집합을 만들어
       new_in_old = 새 자료 묶음 중 기존 자료에도 있는 비율
       old_in_new = 기존 자료 묶음 중 새 자료에도 있는 비율
     (연도·쪽 번호·기한 숫자가 바뀌어도 문장이 같으면 같은 묶음으로 본다)
  3) 표지 연도(새 자료)와 등록된 발행 연도(기존 자료), 표지 제목 유사도
판정: same(같은 자료) / newer(기존 자료의 개정판) / older(기존 자료의 구버전) / overlap(일부 겹침) / none
"""
import difflib
import re
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
ROOT = AGENT_DIR.parent
sys.path.insert(0, str(ROOT))

import rag  # noqa: E402
import store  # noqa: E402

SHINGLE = 4
SAME_MIN = 0.85      # 양쪽 겹침이 이 이상이고 연도가 같으면 같은 자료
EDITION_MIN = 0.30   # 어느 한쪽 겹침이 이 이상이면 같은 자료의 다른 판 후보
TITLE_EDITION = 0.60  # 제목이 이만큼 비슷하면 겹침이 EDITION_MIN의 절반만 넘어도 다른 판 후보
OVERLAP_MIN = 0.10   # 일부 겹침으로 알려 줄 최소값
# 연구보고서 번호("2024 TR-34", "2025-TR-55")는 발행 연도가 아니므로 뺀다
YEAR_RE = re.compile(r"(20[0-3]\d)(?!\s*-?\s*TR)\s*(?:년|학년도|\.|년도|\s|$|_|판|개정)")


def _words(text):
    text = re.sub(r"[0-9０-９]+", " ", text)
    return re.findall(r"[가-힣A-Za-z]+", text)


def shingles(text):
    w = _words(text)
    return {hash(" ".join(w[i:i + SHINGLE])) for i in range(len(w) - SHINGLE + 1)}


def _norm_title(s):
    s = re.sub(r"20[0-3]\d\s*(?:년도|학년도|년)?|[0-9]+\s*판|개정|\(.*?\)|[^가-힣A-Za-z]", "", s or "")
    return s


def pdf_text(pdf_path):
    return rag.page_texts(pdf_path)  # 글자층이 없는 쪽은 OCR 결과로


# 본문에서 판(발행 연도)을 알려 주는 표현: "2025학년도", "2026년 3월 개정", "2024. 12. 발행"
BODY_YEAR_RE = re.compile(
    r"(20[0-3]\d)\s*학년도"
    r"|(20[0-3]\d)\s*년\s*(?:\d{1,2}\s*월\s*)?(?:\d{1,2}\s*일\s*)?(?:개정|발행|발간|시행)"
    r"|(20[0-3]\d)\s*\.\s*\d{1,2}\s*\.?\s*(?:\d{1,2}\s*\.?\s*)?(?:개정|발행|발간)")


REPORT_NO_RE = re.compile(r"(?:연구\s*보고|연구보고서|기본\s*연구|수시\s*연구)\s*(\d{2})\s*-")


def _snippet(text, start, end, pad=18):
    return " ".join(text[max(0, start - pad):end + pad].split())


def _pick(found, latest=False):
    """found: [(연도, 쪽, 문맥)] → 가장 많이 나온 연도(같으면 늦은 해)와 그 첫 쪽·문맥.
    latest=True면 가장 늦은 해(부칙의 개정 이력처럼 여러 해가 나열된 발행 정보는 마지막 개정이 그 판이다)."""
    years = [y for y, _, _ in found]
    best = max(years) if latest else max(set(years), key=lambda y: (years.count(y), y))
    first = next(f for f in found if f[0] == best)
    return best, first[1], first[2], years.count(best)


def year_info(pages, file_name="", title="", stored=None):
    """발행 연도와 그 출처. 표지(1쪽) → 발행 정보(마지막 3쪽) → 본문 표현 → 등록 정보(stored) → 파일 이름 → 제목 순.
    2~3쪽은 '2023년에 개발한 ○○ 가이드'처럼 다른 자료 이야기가 많아 표지로 보지 않고, 본문 표현 규칙으로만 본다.
    돌려주는 값: {"year", "source", "detail", "weak"}  weak=True면 PDF 글자가 아닌 곳에서 읽은 것(확인 필요)."""
    found = [(int(m.group(1)), 1, _snippet(pages[0], m.start(), m.end()))
             for m in YEAR_RE.finditer((pages[0] if pages else "") + " ")]
    if found:
        y, pg, ctx, n = _pick(found)
        return {"year": y, "source": f"표지 p.{pg}", "detail": ctx, "weak": False}
    # 표지에 네 자리 연도가 없고 "연구보고 25-위센터02"처럼 두 자리 연도가 든 보고서 번호만 있으면 그 해(2025)
    m = REPORT_NO_RE.search(pages[0] if pages else "")
    if m:
        return {"year": 2000 + int(m.group(1)), "source": "표지 p.1 보고서 번호",
                "detail": _snippet(pages[0], m.start(), m.end()), "weak": False}

    def body_hits(idx):
        out = []
        for i in idx:
            for m in BODY_YEAR_RE.finditer(pages[i]):
                y = next(g for g in m.groups() if g)
                out.append((int(y), i + 1, _snippet(pages[i], m.start(), m.end())))
        return out

    n_pages = len(pages)
    tail = list(range(max(1, n_pages - 3), n_pages))
    for label, idx in (("발행 정보", tail), ("본문", [i for i in range(1, n_pages) if i not in tail])):
        found = body_hits(idx)
        if found:
            y, pg, ctx, n = _pick(found, latest=label == "발행 정보")
            return {"year": y, "source": f"{label} p.{pg}", "detail": ctx + (f" (같은 연도 {n - 1}곳 더)" if n > 1 else ""),
                    "weak": False}
    if stored:
        return {"year": stored, "source": "등록 정보", "detail": "등록할 때 저장한 발행 연도", "weak": True}
    y = year_from_text(file_name)
    if y:
        return {"year": y, "source": "파일 이름", "detail": file_name, "weak": True}
    y = year_from_text(title)
    if y:
        return {"year": y, "source": "제목", "detail": title, "weak": True}
    return {"year": None, "source": "찾지 못함", "detail": "", "weak": True}


def cover_year(pages, file_name=""):
    """(이전 이름 유지) 표지 → 본문 → 파일 이름 순의 연도."""
    return year_info(pages, file_name)["year"]


def year_from_text(*texts):
    """제목·파일 이름의 '2025', '2026학년도' 같은 연도. 여러 개면 가장 늦은 해."""
    ys = [int(y) for t in texts if t for y in re.findall(r"(?<!\d)(20[0-3]\d)(?!\d)", t)]
    return max(ys) if ys else None


def pdf_year(pdf_path, pages=None, title=""):
    """표지 글자 → 본문 표현 → 파일 이름 → 제목 순으로 발행 연도를 찾는다(표지가 그림이라 글자가 없는 PDF 대비)."""
    pages = pages if pages is not None else pdf_text(pdf_path)
    return year_info(pages, Path(pdf_path).name, title)["year"]


def doc_pages(con, doc_id):
    """등록 자료의 쪽별 본문(청크를 쪽 단위로 이어 붙임). 목록 위치 = 쪽 번호 - 1."""
    rows = con.execute("SELECT page, text FROM chunks WHERE doc_id=? ORDER BY page, chunk_id", (doc_id,)).fetchall()
    if not rows:
        return []
    pages = [""] * max(r["page"] for r in rows)
    for r in rows:
        pages[r["page"] - 1] += r["text"] + "\n"
    return pages


def doc_shingles(con, doc_id):
    rows = con.execute("SELECT text FROM chunks WHERE doc_id=? ORDER BY page, chunk_id", (doc_id,)).fetchall()
    return shingles("\n".join(r["text"] for r in rows))


def compare(pdf_path, pages=None, exclude_doc=None):
    """등록된 모든 자료(사용 중지 포함)와 비교해 관련 있는 것만 겹침 큰 순으로 돌려준다."""
    from doc_agent.ingest import sha256
    pages = pages if pages is not None else pdf_text(pdf_path)
    new_sh = shingles("\n".join(pages))
    new_yi = year_info(pages, Path(pdf_path).name)
    new_year = new_yi["year"]
    new_title = " ".join("\n".join(pages[:2]).split())[:200]
    digest = sha256(pdf_path)
    con = store.connect()
    docs = store.documents(con)
    out = []
    for d, m in docs.items():
        if d == exclude_doc:
            continue
        if m.get("file_sha256") == digest:
            out.append({"doc_id": d, "short": m["short"], "title": m["title"], "status": m["status"],
                        "is_base": bool(m["is_base"]), "old_year": m["published_year"], "new_year": new_year,
                        "new_year_info": new_yi, "old_year_info": new_yi,
                        "new_in_old": 1.0, "old_in_new": 1.0, "title_sim": 1.0, "relation": "same",
                        "why": "파일 내용(바이트)이 완전히 같습니다"})
            continue
        old_sh = doc_shingles(con, d)
        if not old_sh or not new_sh:
            continue
        inter = len(new_sh & old_sh)
        a, b = inter / len(new_sh), inter / len(old_sh)
        tsim = difflib.SequenceMatcher(None, _norm_title(new_title)[:60], _norm_title(m["title"])).ratio()
        top = max(a, b)
        old_yi = year_info(doc_pages(con, d), m["file_name"], m["title"], stored=m["published_year"])
        oy = old_yi["year"]
        if top < OVERLAP_MIN and tsim < TITLE_EDITION:
            continue
        edition = top >= EDITION_MIN or (tsim >= TITLE_EDITION and top >= EDITION_MIN / 2)
        if edition and min(a, b) >= SAME_MIN and (not new_year or not oy or new_year == oy):
            rel, why = "same", "본문이 거의 같고 연도도 같습니다"
        elif edition and new_year and oy and new_year > oy:
            rel, why = "newer", f"본문이 많이 겹치고 연도가 더 최근입니다({oy} → {new_year})"
        elif edition and new_year and oy and new_year < oy:
            rel, why = "older", f"본문이 많이 겹치는데 연도가 더 오래됐습니다({new_year} < 등록본 {oy})"
        elif edition:
            rel, why = "overlap", "본문이 많이 겹치지만 연도로 판을 구분할 수 없습니다"
        else:
            rel, why = "overlap", "본문 일부가 겹칩니다"
        out.append({"doc_id": d, "short": m["short"], "title": m["title"], "status": m["status"],
                    "is_base": bool(m["is_base"]), "old_year": oy, "new_year": new_year,
                    "new_year_info": new_yi, "old_year_info": old_yi,
                    "new_in_old": round(a, 3), "old_in_new": round(b, 3), "title_sim": round(tsim, 3),
                    "relation": rel, "why": why})
    con.close()
    order = {"same": 0, "newer": 1, "older": 2, "overlap": 3}
    out.sort(key=lambda r: (order[r["relation"]], -max(r["new_in_old"], r["old_in_new"])))
    return out


def pair(pdf_a, pdf_b):
    """보정용: 두 PDF의 겹침을 직접 잰다(저장소 밖 파일끼리)."""
    pa, pb = pdf_text(pdf_a), pdf_text(pdf_b)
    sa, sb = shingles("\n".join(pa)), shingles("\n".join(pb))
    inter = len(sa & sb)
    return {"a_in_b": round(inter / len(sa), 3), "b_in_a": round(inter / len(sb), 3),
            "year_a": cover_year(pa, Path(pdf_a).name), "year_b": cover_year(pb, Path(pdf_b).name)}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for f in sys.argv[1:]:
        print(f)
        for r in compare(f):
            print("  ", r)
