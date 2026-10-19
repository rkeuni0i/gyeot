"""학교폭력 사안처리 가이드북 + 학교 규정 + 상담 매뉴얼 RAG 핵심 모듈.

자료·청크·임베딩은 SQLite 저장소(store.py, index/gyeot.db)에 있다.
PDF 여러 개 → 쪽 단위 청크 → 하이브리드 검색(Gemini 임베딩 벡터 + BM25 형태소) → Gemini 답변(근거 쪽 인용).
app.py(화면)와 build_index.py(인덱스 생성)가 이 모듈을 같이 쓴다.
"""
import json
import os
import re
from pathlib import Path

import store

ROOT = Path(__file__).resolve().parent

# .env 파일이 있으면 환경변수로 읽어온다 (별도 외부 패키지 없이 내장 지원)
_env_path = ROOT / ".env"
if _env_path.exists():
    try:
        for _line in _env_path.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))
    except Exception:
        pass

# 기본 자료의 처음 설정(저장소를 처음 만들 때 한 번 넣는다). 이후 원본은 저장소(documents 표)다.
# short는 답변 인용([가이드북 p.24], [이산중 p.5])과 화면 표시에 쓴다.
BASE_SEED = {
    "guide": {"name": "2026학년도 학교폭력 사안처리 가이드북", "short": "가이드북",
              "file": "2026학년도 학교폭력 사안처리 가이드북.pdf", "kind": "guide"},
    "esan": {"name": "이산중학교 학생생활규정(2026.8.27 개정)", "short": "이산중",
             "file": "이산중학교 학생생활규정(2026.8.27 개정).pdf", "kind": "school"},
    # 상담 기법 자료(한국청소년상담복지개발원 연구보고서). toc = (인쇄 쪽, 장 이름), offset = PDF 쪽 - 인쇄 쪽
    "cyber": {"name": "사이버 비행 청소년 상담개입 매뉴얼(한국청소년상담복지개발원, 2023)", "short": "사이버상담",
              "file": "2023_사이버 비행 청소년 상담개입 매뉴얼(한국청소년상담복지개발원).pdf", "kind": "manual",
              "offset": 8, "toc": [
                  (1, "Ⅰ. 서론"), (4, "Ⅱ. 이론적 배경 > 사이버 비행의 이해"),
                  (9, "Ⅱ. 이론적 배경 > 사이버 비행의 원인 이론"), (14, "Ⅱ. 이론적 배경 > 국내외 상담개입 프로그램"),
                  (23, "Ⅲ. 연구방법 > 매뉴얼 개발과정"), (26, "Ⅲ. 연구방법 > 현장전문가 사전요구조사"),
                  (36, "Ⅲ. 연구방법 > 현장전문가 포커스그룹 인터뷰"), (47, "Ⅲ. 연구방법 > 청소년 심층인터뷰"),
                  (53, "Ⅲ. 개인상담 매뉴얼 구성 및 운영원리"), (56, "Ⅲ. 개인상담 매뉴얼 내용(초기·중기·후기)"),
                  (60, "Ⅳ. 연구결과 > 전문가 평가 및 자문"), (64, "Ⅳ. 연구결과 > 개인상담 매뉴얼 최종 구성"),
                  (68, "Ⅴ. 요약 및 논의"), (73, "참고문헌"), (80, "부록"), (91, "Abstract")]},
    "digital": {"name": "디지털 미디어 위기 청소년 유형 및 개입 매뉴얼(한국청소년상담복지개발원, 2024)", "short": "디지털상담",
                "file": "2024_디지털 미디어 위기 청소년 유형 및 개입 매뉴얼(한국청소년상담복지개발원).pdf", "kind": "manual",
                "offset": 10, "toc": [
                    (1, "Ⅰ. 서론"), (5, "Ⅱ. 이론적 배경 > 디지털 미디어와 청소년"),
                    (9, "Ⅱ. 이론적 배경 > 위기유형: 과의존"), (14, "Ⅱ. 이론적 배경 > 위기유형: 디지털 미디어 폭력"),
                    (16, "Ⅱ. 이론적 배경 > 위기유형: 온라인 위험행동"), (21, "Ⅲ. 연구방법"),
                    (29, "Ⅳ. 연구결과 > 델파이 조사 결과(유형 분류)"), (46, "Ⅳ. 연구결과 > FGI: 과의존/중독 청소년"),
                    (53, "Ⅳ. 연구결과 > FGI: 디지털 미디어 폭력 청소년"), (63, "Ⅳ. 연구결과 > FGI: 온라인 위험행동 청소년"),
                    (77, "Ⅳ. 연구결과 > 분석결과 정리 및 시사점"), (79, "Ⅳ. 개입 매뉴얼 구성원리"),
                    (81, "Ⅳ. 개입 매뉴얼 내용"), (86, "Ⅴ. 요약 및 논의"), (90, "참고문헌"), (98, "부록"), (123, "Abstract")]},
}
# 기본 자료의 층·주제·발행 정보(저장소를 처음 만들 때 쓴다)
BASE_META = {
    "guide": {"layer": "topic", "topics": ["학교폭력"], "publisher": "교육부·이화여자대학교 학교폭력예방연구소",
              "published_year": 2026},
    "esan": {"layer": "school", "topics": ["학교 운영", "학교폭력"], "publisher": "이산중학교", "published_year": 2026,
             "school_id": "esan"},
    "cyber": {"layer": "topic", "topics": ["디지털·사이버", "상담 기법", "학교폭력"], "publisher": "한국청소년상담복지개발원",
              "published_year": 2023},
    "digital": {"layer": "topic", "topics": ["디지털·사이버", "상담 기법"], "publisher": "한국청소년상담복지개발원",
                "published_year": 2024},
}
BASE_DOCS = set(BASE_SEED)

# 실행 중 쓰는 활성 자료 목록(저장소에서 읽어 채운다). 화면에서 자료를 추가·삭제하면 refresh_docs()로 다시 읽는다.
DOCS = {}
MANUAL_DOCS = []
SHORT_TO_DOC = {}
ADDED_BUDGET = (3, 1, 5)  # 추가 자료 검색 몫: (원 질문 상위 k, 검색어별 k, 최대 청크)


def refresh_docs(create=False):
    """저장소의 활성 자료로 DOCS를 다시 채운다.
    저장소가 없을 때: create=True(build_index.py)면 기존 파일에서 옮기거나 기본 자료로 새로 만들고,
    아니면 DOCS를 비워 두기만 한다(앱은 '저장소 없음'을 보여 준다. 빈 저장소를 몰래 만들지 않는다)."""
    global SHORT_TO_DOC
    if not store.DB_PATH.exists():
        if not create:
            DOCS.clear()
            MANUAL_DOCS.clear()
            SHORT_TO_DOC = {}
            return
        migrate_from_files()
    con = store.connect()
    rows = store.documents(con, status="active")
    con.close()
    DOCS.clear()
    for d, r in rows.items():
        DOCS[d] = {"name": r["title"], "short": r["short"], "file": r["file_name"], "kind": r["kind"],
                   "layer": r["layer"], "topics": r["topics"], "use_in": r["qtypes"], "summary": r["summary"] or "",
                   "school_id": r["school_id"], "is_base": bool(r["is_base"]), "published_year": r["published_year"],
                   "toc": r["toc"], "offset": r["page_offset"]}
    MANUAL_DOCS[:] = [d for d, m in DOCS.items() if m["kind"] == "manual"]
    SHORT_TO_DOC = {m["short"]: d for d, m in DOCS.items()}


def added_docs():
    return [d for d in DOCS if d not in BASE_DOCS]


def migrate_from_files():
    """기존 index/chunks.json + embeddings.npy + data/registry.json을 저장소로 옮긴다(임베딩 재계산 없음)."""
    import numpy as np
    con = store.connect(create=True)
    store.init_db(con)
    con.execute("INSERT OR IGNORE INTO schools VALUES ('esan', '이산중학교', '경기', ?)", (SCHOOL_INFO,))
    for d, m in BASE_SEED.items():
        meta = BASE_META[d]
        store.upsert_document(con, d, short=m["short"], title=m["name"], file_name=m["file"], layer=meta["layer"],
                              kind=m["kind"], school_id=meta.get("school_id"), publisher=meta["publisher"],
                              published_year=meta["published_year"], is_base=1, status="active",
                              toc_json=json.dumps(m.get("toc")) if m.get("toc") else None,
                              page_offset=m.get("offset"), added_at=store.now(), added_by="초기 설정")
        store.set_topics(con, d, meta["topics"])
        store.set_qtypes(con, d, [q for q, spec in QTYPES.items() if any(x[0] == d for x in spec["plan"])])
    reg_path = ROOT / "data" / "registry.json"
    reg = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else {"docs": {}}
    for d, m in reg.get("docs", {}).items():
        store.upsert_document(con, d, short=m["short"], title=m["name"], file_name=m["file"],
                              layer="topic", kind=m.get("role", "reference"), is_base=0, status="active",
                              summary=m.get("summary"), note=m.get("note"), added_at=m.get("added_at"),
                              added_by=m.get("approved_by"))
        store.set_topics(con, d, m.get("topics", []))
        store.set_qtypes(con, d, m.get("use_in", []))
    old_json, old_npy = ROOT / "index" / "chunks.json", ROOT / "index" / "embeddings.npy"
    if old_json.exists() and old_npy.exists():
        chunks = json.loads(old_json.read_text(encoding="utf-8"))["chunks"]
        emb = np.load(old_npy)
        by_doc = {}
        for c, v in zip(chunks, emb):
            by_doc.setdefault(c["doc"], ([], []))
            by_doc[c["doc"]][0].append(c)
            by_doc[c["doc"]][1].append(v)
        for d, (cs, vs) in by_doc.items():
            if con.execute("SELECT 1 FROM documents WHERE doc_id=?", (d,)).fetchone():
                store.replace_chunks(con, d, cs, vs, EMBED_MODEL)
    con.commit()
    con.close()


SCHOOL_NAME = "이산중학교(경기 화성시)"
SCHOOL_INFO = "Wee 클래스 운영, 전문상담교사 1명 배정(학교 홈페이지에서 확인)"
# 학교 주변 연계 기관(상담사가 확인한 기관). 출처: 2025 청소년상담복지센터 현황(2025.10) + 사용자 확인(2026-10-19)
LOCAL_AGENCIES = [
    {"kind": "청소년상담복지센터", "name": "화성시청소년상담복지센터",
     "phone": "031-225-1318", "phone2": "031-267-8771", "fax": "031-267-8774",
     "address": "경기도 화성시 태안로 145 유앤아이센터 2층(병점동)", "homepage": "www.mars1318.or.kr"},
]
LOCAL_AGENCY_TEXT = "\n".join(
    f"- {a['kind']}: {a['name']} · 전화 {a['phone']}, {a['phone2']} · 팩스 {a['fax']} · {a['address']} · {a['homepage']}"
    for a in LOCAL_AGENCIES)
DB_PATH = store.DB_PATH
LOG_PATH = ROOT / "logs" / "chat_log.jsonl"

LLM_MODEL = os.environ.get("MINDGUIDE_MODEL", "gemini-3.8-flash")
EMBED_MODEL = "gemini-embedding-001"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# 목차(3쪽) 기준 구간. PDF 쪽 번호 = 인쇄 쪽 번호.
SECTIONS = [
    (1, 5, "표지·목차"),
    (6, 9, "PART1 학교폭력의 이해 > 1장 학교폭력 개념과 유형"),
    (10, 10, "PART1 학교폭력의 이해 > 2장 사안처리 흐름도"),
    (11, 15, "PART1 학교폭력의 이해 > 3장 사전 예방활동"),
    (16, 19, "PART1 학교폭력의 이해 > 4장 사안처리 시 유의사항 및 관련문의"),
    (20, 34, "PART2 대응 및 사안조사 > 1장 학교의 학교폭력 대응(감지·신고·대응요령)"),
    (35, 43, "PART2 대응 및 사안조사 > 2장 사안조사(전담기구·전담조사관)"),
    (44, 51, "PART2 대응 및 사안조사 > 2장 사안조사(절차 및 방법)"),
    (52, 55, "PART2 대응 및 사안조사 > 2장 사안조사(가해자와 피해학생의 분리)"),
    (56, 59, "PART2 대응 및 사안조사 > 2장 사안조사(긴급조치)"),
    (60, 67, "PART2 대응 및 사안조사 > 2장 사안조사(학생 및 보호자 상담)"),
    (68, 71, "PART2 대응 및 사안조사 > 3장 학교폭력 통합지원(제로센터)"),
    (72, 76, "PART3 자체해결제 및 관계회복 > 1장 학교장 자체해결제"),
    (77, 82, "PART3 자체해결제 및 관계회복 > 2장 관계회복"),
    (83, 91, "PART3 자체해결제 및 관계회복 > 2장 분쟁조정"),
    (92, 102, "PART4 조치결정 및 이행 > 1장 심의위원회의 조치결정"),
    (103, 109, "PART4 조치결정 및 이행 > 2장 피해학생 보호조치"),
    (110, 120, "PART4 조치결정 및 이행 > 2장 가해학생 조치·특별교육"),
    (121, 128, "PART4 조치결정 및 이행 > 3장 학교생활기록부 기재"),
    (129, 138, "PART4 조치결정 및 이행 > 4장 불복절차(행정심판·행정소송)"),
    (139, 140, "참고1 학교폭력 사안에 대한 민사 책임"),
    (141, 144, "참고2 가해학생 조치사항 삭제 심의 절차"),
    (145, 172, "부록1 각종 양식"),
    (173, 182, "부록2 학교 기숙사 내 학교폭력 예방 및 대응"),
    (183, 216, "부록3 학교폭력예방법 및 시행령"),
]

SYSTEM_PROMPT = f"""너는 {SCHOOL_NAME} 전용 사안처리·상담 도우미 '곁'이다. 이 학교는 {SCHOOL_INFO}이며, 너는 그 1인 전문상담교사(Wee 클래스 상담사)를 돕는다.
너는 학교 규정, 절차, 기한, 법적 의무, 서류·양식, 조치 종류, 외부 기관 연계를 정확히 알려주고, 상담 매뉴얼에 근거한 상담 개입 방법과 학생 눈높이 설명을 돕는다.

[자료 종류]
- [이산중]: @@esan@@. 이 학교의 생활지도·상담·학생생활교육위원회·징계·출결 규정.
- [가이드북]: @@guide@@. 교육부·법령 기반의 전국 공통 학교폭력 사안처리 절차와 학교폭력 정의·유형.
- [사이버상담]: @@cyber@@. 사이버 비행(사이버폭력 가해) 청소년의 개념·유형·사례와 개인상담 단계·활동.
- [디지털상담]: @@digital@@. 디지털 미디어 폭력 피해·가해, 과의존, 온라인 위험행동 청소년의 특성과 개입.
- [지역기관]: 이 학교가 연계하는 지역 기관(상담사가 확인한 목록, 쪽 번호 없음).
{LOCAL_AGENCY_TEXT}
매번 [질문 유형]과 [확인 순서], [답변 형식]이 함께 주어진다. 확인 순서의 1순위 자료에서 답의 중심을 찾고, 다음 순위 자료로 보충한다.

[답변 규칙]
1. 반드시 아래 [자료]에 있는 내용으로만 답한다. 자료에 없는 사실(처벌 수위, 통계, 기관 번호 등)은 만들지 않는다. 답이 없으면 "이산중 규정과 가이드북, 상담 매뉴얼에서 확인되지 않습니다"라고 쓴 뒤, 학교 규정이면 학교 생활지도 담당 부서, 학교폭력이면 교육지원청(학교폭력 제로센터) 확인을 권한다.
1-1. 질문과 관련된 내용이 여러 쪽(본문, 부록 양식의 작성 요령, 법령 조문, 매뉴얼의 유형표·사례)에 흩어져 있으면 빠짐없이 모아 답한다. 기한·기간·횟수·인원 같은 숫자와 예외 조건(예: 피해학생이 반대하는 경우)은 빠뜨리지 않는다. 다만 질문과 관련 없는 내용은 넣지 않는다.
2. 사실을 담은 문장 끝에는 근거를 [이산중 p.5], [가이드북 p.24], [사이버상담 p.15]처럼 자료 이름과 쪽 번호로 단다. 자료 블록 머리의 이름과 쪽 번호만 쓴다. 학생에게 하는 말 예시도 그 내용이 나온 쪽을 단다.
3. 답변 구성은 주어진 [답변 형식]을 따른다. 형식에 없는 섹션을 억지로 만들지 않는다.
4. 이산중 규정은 [질문 유형]이 "학교 맞춤형"일 때는 답의 첫 부분이 된다. 다른 유형에서는 질문과 직접 관련된 조항이 [자료]에 있을 때만 짧게 덧붙이고, 관련이 없으면 이산중 규정을 언급하지 않는다.
5. 학교폭력 사안(신고·조사·분리·심의·조치)은 학교폭력예방법에 따른 법정 절차이므로 절차를 안내할 때는 가이드북 절차를 빠짐없이 쓴다. 학교폭력에 해당하지 않는 생활지도 위반은 이산중 학생생활교육규정(학생생활교육위원회)을 따른다. 이산중 규정과 법령 내용이 다르면 둘 다 쓰고 법령이 우선함을 밝힌다.
6. 상담 개입 내용은 상담 매뉴얼과 가이드북의 상담 요령에 있는 내용만 쓰고, 일반적인 상담 이론이나 자료에 없는 기법을 덧붙이지 않는다. 매뉴얼은 청소년상담복지센터 개인상담 기준이므로 학교 상황에 맞게 조정이 필요할 수 있다는 점을 한 줄 덧붙인다.
7. 학교 규정은 "이산중 학생생활교육규정 제18조"처럼 규정명과 조문을, 법 조항은 "학교폭력예방법 제16조"처럼 법령명과 조문을 쓴다.
8. 전화번호·기한·조치 번호·징계 단계는 자료에 적힌 그대로 쓴다. 바꾸거나 지어내지 않는다. 위 [지역기관] 목록의 기관명·전화번호는 자료로 보고 그대로 쓸 수 있다.
8-1. [자료]가 청소년상담복지센터나 외부 상담기관(학교 밖 청소년 지원 포함) 연계를 안내하면, 일반 명칭만 쓰지 말고 이 학교의 연계 기관인 화성시청소년상담복지센터(031-225-1318)로 구체적으로 안내하고 끝에 [지역기관]을 단다. 연계 근거(어떤 경우에 연계하는지)는 [자료]의 쪽 번호로 단다. Wee센터·경찰·아동보호전문기관 등 다른 종류의 기관을 이 센터로 바꿔 쓰지 않는다.
9. 성폭력 관련 사안이면 "피해학생 의사와 관계없이 수사기관 즉시 신고 의무"를 맨 앞에 쓴다.
10. 자해·자살 위험 신호가 질문에 있으면 학교 위기관리 절차와 Wee센터·청소년상담 1388 등 전문기관 연계가 우선임을 먼저 알린다. [자료]에 학교 위기대응 자료(추가 자료 중 위기 대응을 다루는 것)가 있으면 그 자료의 절차만 근거로 안내하고, 없으면 "자해·자살 관련 상담 방법은 등록된 자료에서 확인되지 않습니다"라고 밝힌다. 학교폭력이나 사이버 비행 등 다른 상황의 상담 요령을 자해·자살 상담 방법처럼 옮겨 쓰지 않는다.
11. 상담사에게 쓰는 문장은 존댓말로 간결하게 쓴다. 단, [질문 유형]이 "학생 눈높이 설명"이면 상담사가 학생에게 그대로 읽어 줄 수 있는 반말·쉬운 말 예시를 따옴표로 쓴다. 예시 문장도 자료에 있는 정의·유형·사례만 쉬운 말로 바꾼 것이어야 한다. 굵은 글씨 규칙은 12번을 따른다.
12. 상담사가 답을 훑어만 봐도 무엇을 해야 하는지 보이도록 굵은 글씨를 쓴다.
 - 첫 줄 요약에서는 결론(해야 할 일·가능 여부)을 굵게 한다.
 - 목록 항목마다 핵심 1~2곳만 굵게 한다: 해야 할 행동, 기한·기간·횟수 같은 숫자, 담당자, 기관명·전화번호, 법정 의무·금지("즉시 신고", "누설 금지" 등).
 - 문장 전체나 근거 표시([자료 p.쪽])는 굵게 하지 않는다. 굵은 글씨 바로 뒤에는 조사를 붙이지 않는다(예: "**48시간 이내**에" 대신 "**48시간 이내** 보고").
 - 한 섹션 안에서 "주제", "세부 활동"처럼 묶음 이름이 필요하면 번호 목록 항목으로 쓰지 말고 "### 묶음 이름" 소제목으로 쓴다. 번호 목록은 순서가 있는 단계에만 쓴다.
"""


def system_prompt():
    """기본 자료 이름은 저장소의 현재 제목으로 채운다(개정판으로 교체하면 새 제목).
    삭제·사용 중지된 기본 자료는 [자료 종류] 줄을 뺀다(없는 자료를 근거로 쓰지 않게)."""
    lines = []
    for line in SYSTEM_PROMPT.split("\n"):
        m = re.search(r"@@(\w+)@@", line)
        if m and m.group(1) not in DOCS:
            continue
        if m:
            line = line.replace(m.group(0), DOCS[m.group(1)]["name"])
        lines.append(line)
    return "\n".join(lines)

# 질문 유형별 확인 순서와 검색 몫: (문서, 원 질문 상위 k, 검색어별 k, 최대 청크)
QTYPES = {
    "school": {
        "label": "학교 맞춤형",
        "desc": "이산중학교의 규칙·절차·처리 방법(생활지도, 징계와 그 집행, 출석정지·특별교육 이수 시 출결, 학생생활교육위원회, 상담 시간·비밀, 휴대전화 등)을 묻는 질문. 학교폭력 사안이라고 밝히지 않은 징계·출결·생활지도 질문은 여기에 넣는다",
        "plan": [("esan", 6, 3, 12), ("guide", 8, 5, 24), ("cyber", 3, 1, 4), ("digital", 3, 1, 4)],
        "format": """- 첫 줄: 한두 문장 핵심 요약(학교폭력인지 생활지도 사안인지 포함)
- "## 이산중학교 규정": 관련 조항과 내용. 관련 조항이 없으면 "이산중 규정에는 관련 조항이 없어 가이드북 기준으로 안내합니다" 한 줄만 쓴다.
- "## 지금 해야 할 일": 번호 목록. 이산중 절차를 먼저, 가이드북 절차로 보충. 기한과 담당을 함께 쓴다.
- "## 연계 기관·연락처" / "## 유의사항" / "## 필요한 양식": 필요할 때만.""",
    },
    "procedure": {
        "label": "학교폭력 절차·법",
        "desc": "학교폭력 사안의 신고·보고·조사·분리·긴급조치·자체해결·심의·조치·불복 절차, 기한, 요건, 법적 의무, 양식을 묻는 질문(학교폭력·괴롭힘·성폭력 등 사안이 질문에 드러난 경우)",
        "plan": [("guide", 8, 5, 24), ("esan", 4, 2, 6), ("cyber", 2, 1, 3), ("digital", 2, 1, 3)],
        "format": """- 기한·요건·조치 종류처럼 사실을 묻는 질문이면 원문 표현을 살려 바로 답한다(조문이면 각 호까지). 절차 섹션은 쓰지 않는다.
- 상황을 설명하며 무엇을 해야 하는지 묻는 질문이면:
  - 첫 줄: 핵심 요약
  - "## 지금 해야 할 일": 가이드북 법정 절차를 순서대로, 기한과 담당 포함.
  - "## 이산중학교 규정": 관련 조항이 [자료]에 있을 때만.
  - "## 상담 개입 참고": 상담 방향이 필요할 때만, 매뉴얼 근거로.
  - "## 연계 기관·연락처" / "## 유의사항" / "## 필요한 양식": 필요할 때만.""",
    },
    "definition": {
        "label": "용어·개념 정의",
        "desc": "어떤 용어나 개념(학교폭력, 사이버폭력, 사이버 비행, 따돌림, 딥페이크 등)이 무엇인지, 무엇이 포함되는지를 묻는 질문",
        "plan": [("guide", 6, 3, 12), ("cyber", 4, 2, 7), ("digital", 4, 2, 7), ("esan", 3, 1, 4)],
        "format": """- "## 법적 정의": 법령·가이드북에 정의가 있으면 원문 그대로(조문 번호 포함). 없으면 이 섹션을 쓰지 않는다.
- "## 상담·연구 자료의 정의와 유형": 매뉴얼의 정의와 하위 유형(표에 있으면 유형별 한 줄씩).
- "## 비슷한 용어와 차이": 자료에 구분이 나오면.
- 이산중 규정에 직접 관련된 조항이 있으면 마지막에 한 줄.""",
    },
    "counseling": {
        "label": "상담 가이드",
        "desc": "학생(가해·피해·목격)이나 보호자를 어떻게 상담할지, 회기 구성, 개입 방법, 다룰 내용을 묻는 질문(학교 절차를 함께 물어도 상담 방법이 들어 있으면 여기에 넣는다)",
        "plan": [("cyber", 5, 2, 9), ("digital", 4, 2, 7), ("guide", 8, 4, 18), ("esan", 3, 1, 4)],
        "format": """- 첫 줄: 핵심 요약
- "## 상담 개입 참고": 단계·주제·활동을 순서대로, 매뉴얼과 가이드북 상담 요령 근거로. 학교 조정 필요 한 줄 포함.
- 질문이 학교 절차도 함께 물으면 "## 지금 해야 할 일"에 가이드북 법정 절차(신고·보고 기한, 분리·접촉 금지 조치, 증거 확보 등)를 기한·담당과 함께 빠짐없이 쓰고, 관련 이산중 규정이 있으면 덧붙인다.
- 절차를 묻지 않았으면 "## 학교에서 함께 챙길 절차"에 신고·보고 의무 등 관련된 것만 짧게.""",
    },
    "student_explain": {
        "label": "학생 눈높이 설명",
        "desc": "어떤 개념·규칙·위험을 학생에게 쉽게 설명하거나 교육하려면 뭐라고 말하면 되는지 묻는 질문",
        "plan": [("cyber", 5, 2, 9), ("digital", 5, 2, 9), ("guide", 6, 3, 12), ("esan", 3, 1, 4)],
        "format": """상담사가 학생에게 그대로 읽어 줄 수 있는 말을 중심으로 쓴다.
- "## 한 줄로 설명하기": 쉬운 반말 한두 문장(따옴표) + 근거.
- "## 이렇게 비유해 보세요": 교실·현실 행동과 비교하는 문장(따옴표). 자료의 정의를 벗어나지 않는다.
- "## 학생이 겪을 수 있는 예시": 자료의 유형표·사례에서 골라 학생이 아는 장면(단톡방, SNS, 게임 등)으로 바꿔 3~5개, 각 줄에 근거.
- "## 꼭 알려줄 점": 자료에 근거가 있는 핵심 2~3개(예: 장난이어도 폭력이 될 수 있음).
- 이산중 규정에 학생이 지켜야 할 관련 조항이 있으면 "## 우리 학교 규칙" 한두 줄.
- "## 상담사 참고": 근거가 된 공식 정의를 한두 줄로.""",
    },
    "crisis": {
        "label": "위기(자해·자살)",
        "desc": "자해·자살 위험이나 그 상담 방법을 묻는 질문",
        "plan": [("esan", 4, 2, 6), ("guide", 6, 3, 12), ("cyber", 2, 1, 3), ("digital", 2, 1, 3)],
        "format": "규칙 10을 따른다. 위기 대응 자료가 [자료]에 있으면 그 절차를 단계별로, 없으면 연계 기관과 학교 절차만 짧게 쓴다.",
    },
}
DEFAULT_QTYPE = "school"


def plan_for(qtype, index=None, topics=None):
    """유형의 기본 확인 순서(사용 중지된 기본 자료는 뺌) 뒤에 추가 자료를 붙인다.
    추가 자료는 그 유형에 쓰도록 등록됐고, 층이 공통(common)·학교(school)이거나 질문 주제와 겹칠 때만 붙인다
    (주제를 판단하지 못했으면 주제 조건은 보지 않는다). 인덱스에 청크가 있는 자료만 쓴다."""
    indexed = set(index.doc_of) if index is not None else None
    plan = [x for x in QTYPES[qtype]["plan"] if x[0] in DOCS and (indexed is None or x[0] in indexed)]
    for d in added_docs():
        m = DOCS[d]
        topic_ok = not topics or m.get("layer") in ("common", "school") or set(topics) & set(m.get("topics", []))
        if qtype in m.get("use_in", []) and topic_ok and (indexed is None or d in indexed):
            if qtype == "crisis":  # 기본 자료에 위기 대응 자료가 없으므로, 등록된 위기 대응 자료를 1순위로 크게 본다
                plan.insert(0, (d, 5, 2, 9))
            else:
                plan.append((d, *ADDED_BUDGET))
    return plan


CRISIS_RE = re.compile(r"자살|자해|죽고\s?싶|죽을래|목숨|손목|그었|유서|뛰어내")
SEXUAL_RE = re.compile(r"성폭력|성폭행|성추행|성희롱|불법\s?촬영|딥페이크|몰카|음란")

_kiwi = None
_client = None


# ---------- 텍스트 전처리 ----------
def section_of(page):
    """가이드북 쪽 → 목차 기준 장 이름."""
    for start, end, name in SECTIONS:
        if start <= page <= end:
            return name
    return ""


def clean_page_text(text):
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if len(s) <= 1:  # 옆면 탭 글자(학/교/폭/력 …)와 빈 줄 제거
            continue
        if s in ("학교폭력 사안처리 가이드북",) or re.fullmatch(r"\d{3}", s) or re.fullmatch(r"PART\.\s?\d.*", s):
            continue
        lines.append(s)
    return "\n".join(lines)


OCR_DIR = ROOT / "data" / "ocr"   # 글자를 뽑을 수 없는 PDF의 OCR 결과(파일 내용 해시별 JSON). doc_agent/ocr.py가 만든다
OCR_MIN_CHARS = 30                  # 이보다 글자가 적은 쪽은 OCR 결과로 채운다


def file_sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def ocr_cache(path):
    """그 PDF의 OCR 결과 {"engine", "pages": [...], ...} 또는 None. 파일 이름이 아니라 내용 해시로 찾는다."""
    p = OCR_DIR / f"{file_sha256(path)[:32]}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def page_texts(path):
    """PDF 쪽별 본문. 글자층이 없는 쪽(그림·윤곽선)은 OCR 결과가 있으면 그것으로 채운다."""
    import pymupdf
    doc = pymupdf.open(path)
    pages = [clean_page_text(p.get_text()) for p in doc]
    cache = ocr_cache(path)
    if cache:
        for i, t in enumerate(cache["pages"][:len(pages)]):
            if len(pages[i]) < OCR_MIN_CHARS and t:
                pages[i] = clean_page_text(t)
    return pages


def split_text(text, size=900, overlap=150):
    if len(text) <= size * 1.3:
        return [text]
    parts, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        # 줄 경계에서 자르기
        cut = text.rfind("\n", start + size // 2, end)
        if end < len(text) and cut > 0:
            end = cut
        parts.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return parts


# ---------- 형태소 토큰화 ----------
def tokenize(text):
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi
        _kiwi = Kiwi()
    keep = ("NNG", "NNP", "NNB", "NR", "SL", "SN", "VV", "VA", "XR", "SH")
    return [t.form for t in _kiwi.tokenize(text) if t.tag.startswith(keep) and len(t.form) > 0]


# ---------- 인덱스 ----------
def school_sections(doc):
    """학교 규정 쪽 → '편 > 장' 이름. 쪽 안의 편(Ⅰ~Ⅴ)·장 제목을 차례로 따라간다."""
    part, chapter, out = "", "", []
    for i, page in enumerate(doc):
        t = page.get_text()
        if i == 0:
            out.append("표지·목차")
            continue
        parts = re.findall(r"(Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ)\.\s*([^\n·]{2,20})", t)
        chapters = re.findall(r"제\s*\d+\s*장\s*([^\n]{2,20})", t)
        if parts:
            part, chapter = f"{parts[0][0]}. {parts[0][1].strip()}", ""
        if re.search(r"서식\s*\d", t):
            part, chapter = "Ⅴ. 부록(서식)", ""
            chapters = []
        names = [re.sub(r"\s+", " ", c).strip() for c in chapters]
        # 쪽 안에 장이 여러 개 시작하면 모두 적고, 다음 쪽에는 마지막 장을 이어 쓴다
        label = " · ".join([chapter] + names if chapter and names and t.lstrip()[:30].find("제") != 0 else names) or chapter
        if names:
            chapter = names[-1]
        out.append(f"{part} > {label}" if label else part)
    return out


def manual_section(meta, pdf_page):
    """연구보고서 매뉴얼: 인쇄 목차 쪽 + offset으로 장 이름을 정한다."""
    label = "표지·초록·목차"
    for start, name in meta["toc"]:
        if pdf_page >= start + meta["offset"]:
            label = name
    return label


def generic_sections(doc, texts=None):
    """추가 자료: 쪽 앞부분의 'Ⅰ. 제목', '제1장 제목', '1. 제목' 같은 줄을 장 이름으로 이어 쓴다.
    texts: 쪽별 본문(OCR로 채운 것). 없으면 PDF 글자층에서 읽는다."""
    part, sec, out = "", "", []
    for text in (texts if texts is not None else (pg.get_text() for pg in doc)):
        lines = [x.strip() for x in text.splitlines() if x.strip()][:10]
        for line in lines:
            if re.match(r"^(Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ|Ⅵ|Ⅶ|Ⅷ|Ⅸ|Ⅹ)\.\s*\S.{1,28}$", line) or re.match(r"^제\s*\d+\s*장\s*\S.{1,24}$", line):
                part, sec = line, ""
            elif part and re.match(r"^\d\.\s*\S.{1,28}$", line):
                sec = line
        out.append(f"{part} > {sec}" if sec else part)
    return out


def doc_chunks(doc_id, meta):
    """한 자료의 청크. meta: DOCS 항목과 같은 모양(file, kind, toc, offset)."""
    import pymupdf
    chunks = []
    doc = pymupdf.open(ROOT / "data" / meta["file"])
    texts = page_texts(ROOT / "data" / meta["file"])  # 글자층이 없는 쪽은 OCR 결과로
    # 가이드북 장 구간표(section_of)는 2026학년도 판 기준이라, 개정판으로 바꾸면 일반 규칙을 쓴다
    fixed_guide = doc_id == "guide" and meta["file"] == BASE_SEED["guide"]["file"]
    sections = (school_sections(doc) if meta["kind"] == "school"
                else None if meta.get("toc") or fixed_guide else generic_sections(doc, texts))
    for i, text in enumerate(texts):
        pno = i + 1
        if len(text) < 30:
            continue
        if meta.get("toc"):
            section = manual_section(meta, pno)
        else:
            section = sections[i] if sections else section_of(pno)
        for j, part in enumerate(split_text(text)):
            chunks.append({"id": f"{doc_id}:p{pno}-{j}", "doc": doc_id, "page": pno,
                           "section": section, "text": part})
    return chunks


def build_chunks(only=None):
    """only: 이 문서 ID 목록만 청크로 만든다(자료 추가 시 증분 인덱싱용)."""
    chunks = []
    for doc_id, meta in DOCS.items():
        if only and doc_id not in only:
            continue
        chunks += doc_chunks(doc_id, meta)
    return chunks


def embed_texts(texts, batch=90):
    """Gemini 임베딩 → L2 정규화된 float32 행렬(코사인 유사도 = 내적)."""
    import numpy as np
    vecs = []
    for i in range(0, len(texts), batch):
        resp = client().embeddings.create(model=EMBED_MODEL, input=texts[i:i + batch])
        vecs.extend(d.embedding for d in resp.data)
    m = np.asarray(vecs, dtype="float32")
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def chunk_embed_text(c):
    return f"{DOCS[c['doc']]['name']} · {c['section']}\n{c['text']}"


class Index:
    RRF_K = 60

    def __init__(self):
        """저장소에서 활성 자료의 청크·임베딩을 읽어 BM25와 벡터 검색을 준비한다."""
        import numpy as np
        from rank_bm25 import BM25Okapi
        refresh_docs()
        con = store.connect()
        self.chunks, self.emb = store.load_chunks(con, list(DOCS))
        con.close()
        self.bm25 = BM25Okapi([c["tokens"] for c in self.chunks])
        # 법령 원문(부록3)은 본문 설명과 중복되므로 순위를 조금 낮춘다
        self.penalty = np.array([0.85 if c["doc"] == "guide" and c["section"].startswith("부록3")
                                 else 0.5 if c["section"] in ("참고문헌", "Abstract", "표지·초록·목차")
                                 # 매뉴얼의 연구방법·델파이·전문가 평가 쪽은 상담 개입 내용이 아니므로 낮춘다
                                 else 0.8 if c["doc"] in MANUAL_DOCS and (c["section"].startswith("Ⅲ. 연구방법")
                                                                       or "델파이" in c["section"] or "전문가 평가" in c["section"])
                                 else 1.0
                                 for c in self.chunks])
        self.doc_of = np.array([c["doc"] for c in self.chunks])
        # 가이드북 부록 양식: "[양식2-4]"가 시작되는 쪽부터 다음 양식 전까지를 그 양식의 쪽으로 본다
        starts = {}
        for c in self.chunks:
            if c["doc"] == "guide" and c["section"].startswith("부록1"):
                for f in re.findall(r"\[\s*양식\s*(\d+-\d+)\s*\]", c["text"]):
                    starts.setdefault(f, c["page"])
        order = sorted(starts.items(), key=lambda kv: kv[1])
        self.form_pages = {f: list(range(p, (order[i + 1][1] if i + 1 < len(order) else p + 1)))
                           for i, (f, p) in enumerate(order)}

    def page_hits(self, doc, page, qvec, **extra):
        """특정 쪽의 청크들을 검색 결과 형식으로 만든다(코사인은 질문 벡터 기준)."""
        out = []
        for idx, c in enumerate(self.chunks):
            if c["doc"] == doc and c["page"] == page:
                cos = float(self.emb[idx] @ qvec) if self.emb is not None and qvec is not None else float("nan")
                out.append({**{k: v for k, v in c.items() if k != "tokens"}, "cos": cos, "bm25": 0.0,
                            "vec_rank": 0, "bm25_rank": 0, "score": 0.0, **extra})
        return out

    def search(self, query, k=6, qvec=None, doc=None):
        """하이브리드 검색. doc을 주면 그 문서 안에서만 찾는다(순위도 문서 안 기준).
        각 결과에 cos(벡터 코사인 유사도), bm25, 두 순위, 합산 점수(RRF)를 담는다."""
        import numpy as np
        idxs = np.arange(len(self.chunks)) if doc is None else np.where(self.doc_of == doc)[0]
        n = len(idxs)
        q = tokenize(query)
        bm = (self.bm25.get_scores(q) if q else np.zeros(len(self.chunks)))[idxs]
        bm_rank = np.empty(n, int)
        bm_rank[np.argsort(-bm)] = np.arange(1, n + 1)
        if self.emb is not None:
            if qvec is None:
                qvec = embed_texts([query])[0]
            cos = self.emb[idxs] @ qvec
            vec_rank = np.empty(n, int)
            vec_rank[np.argsort(-cos)] = np.arange(1, n + 1)
            rrf = 1 / (self.RRF_K + vec_rank) + 1 / (self.RRF_K + bm_rank)
        else:
            cos, vec_rank = np.full(n, np.nan), np.full(n, 0)
            rrf = 1 / (self.RRF_K + bm_rank)
        rrf = rrf * self.penalty[idxs]
        hits = []
        for local in np.argsort(-rrf)[:k]:
            idx = idxs[local]
            hits.append({**{key: v for key, v in self.chunks[idx].items() if key != "tokens"},
                         "cos": float(cos[local]), "bm25": float(bm[local]),
                         "vec_rank": int(vec_rank[local]), "bm25_rank": int(bm_rank[local]),
                         "score": float(rrf[local])})
        return hits


# ---------- LLM ----------
def client():
    global _client
    if _client is None:
        from openai import OpenAI
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("환경변수 GEMINI_API_KEY가 없습니다.")
        _client = OpenAI(api_key=key, base_url=GEMINI_BASE_URL)
    return _client


def cite_label(doc, page):
    return f"{DOCS[doc]['short']} p.{page}"


def format_context(hits, doc_order=None):
    order = {d: i for i, d in enumerate(doc_order or ["esan", "guide"] + MANUAL_DOCS)}
    blocks = []
    for h in sorted(hits, key=lambda x: (order.get(x["doc"], 9), x["page"])):  # 확인 순서대로 보여준다
        blocks.append(f"<<{cite_label(h['doc'], h['page'])} | {DOCS[h['doc']]['name']} | {h['section']}>>\n{h['text']}")
    return "\n\n".join(blocks)


SHORT_TO_DOC = {m["short"]: d for d, m in DOCS.items()}


def parse_citations(text):
    """답변의 [가이드북 p.24, p.25], [이산중 p.5] → [("guide", 24), ("guide", 25), ("esan", 5)].
    괄호 안에서 이름 없이 쓴 p.N은 앞의 이름을 따르고, 이름이 전혀 없으면 가이드북으로 본다."""
    found = []
    shorts = sorted(SHORT_TO_DOC, key=len, reverse=True)  # '정서행동2026'이 '정서행동'보다 먼저 맞도록
    for inner in re.findall(r"\[([^\[\]]*p\.\s?\d[^\[\]]*)\]", text):
        cur = "guide"
        for part in inner.split(","):
            hit = next((s for s in shorts if s in part), None)
            if hit:
                cur = SHORT_TO_DOC[hit]
            for p in re.findall(r"p\.\s?(\d{1,3})", part):
                if cur in DOCS and (cur, int(p)) not in found:
                    found.append((cur, int(p)))
    return sorted(found, key=lambda x: (x[0] != "esan", x[1]))


QUERY_PROMPT = """너는 이산중학교 학생생활규정, 학교폭력 사안처리 가이드북, 상담 매뉴얼의 검색어 생성기이자 질문 유형 분류기다.
[이전 질문]과 [현재 질문]을 받는다.
1) standalone: 현재 질문을 혼자 읽어도 뜻이 통하는 한 문장으로 다시 쓴다.
   - 현재 질문이 이전 질문에 이어지는 후속 질문일 때만(예: "그럼 양식은?", "그 기간은?") 이전 질문 맥락을 반영한다.
   - 현재 질문이 새로운 주제면 이전 질문을 완전히 무시하고 현재 질문 그대로 쓴다.
2) queries: 가이드북에서 찾아야 할 검색어 2~5개. 가이드북 용어를 쓴다
   (예: 학교폭력 정의, 학교폭력 유형, 신고 접수, 교육지원청 보고, 가해자와 피해학생 분리, 긴급조치, 학교장 자체해결 요건,
   관계회복, 심의위원회, 피해학생 보호조치, 가해학생 조치, 행정심판, 집행정지, 사이버폭력 증거, 비밀보장,
   성폭력 수사기관 신고, 외부기관 연계, 양식).
   학교 규정이 관련된 질문이면 규정 용어도 넣는다
   (예: 상담 요청, 수업 중 상담, 상담 비밀, 학생생활교육위원회, 출석정지, 특별교육 이수, 출결처리, 개별학생교육지원, 사후지도, 휴대전화 분리 보관).
   상담 방법을 묻는 질문이면 상담 매뉴얼 용어도 넣는다
   (예: 사이버 비행 청소년 개인상담, 상담 초기 관계형성, 상담 구조화, 재비행 방지, 보호자 상담, 디지털 미디어 폭력 피해 청소년 효과적인 개입, 정서조절, 인지 왜곡).
   학생에게 설명하는 질문이면 매뉴얼의 유형·사례 용어도 넣는다(예: 사이버 비행 유형, 사이버 언어폭력, 사이버 따돌림, 사이버 갈취 사례).
3) type: 질문 유형을 아래에서 하나 고른다.
{TYPE_LINES}
4) topics: 질문과 관련된 주제를 아래에서 0~3개 고른다: {TOPIC_LINE}
JSON 객체만 출력한다. 예: {"standalone": "학교폭력예방법상 학교폭력의 정의는?", "queries": ["학교폭력 정의", "학교폭력예방법 제2조"], "type": "definition", "topics": ["학교폭력"]}"""
QUERY_PROMPT = (QUERY_PROMPT.replace("{TYPE_LINES}", "\n".join(f"   - {k}: {v['desc']}" for k, v in QTYPES.items()))
                .replace("{TOPIC_LINE}", ", ".join(store.TOPICS)))


def rewrite_query(question, history=None, model=LLM_MODEL):
    """→ (standalone 질문, 검색어 목록, 질문 유형, 주제 목록). 실패하면 (원 질문, [], 기본 유형, [])."""
    prev = [m["content"] for m in (history or []) if m["role"] == "user"][-1:]
    user = f"[이전 질문]\n{prev[0][:1500] if prev else '(없음)'}\n\n[현재 질문]\n{question}"
    try:
        resp = client().chat.completions.create(
            model=model, temperature=0,
            messages=[{"role": "system", "content": QUERY_PROMPT}, {"role": "user", "content": user}])
        raw = resp.choices[0].message.content or ""
        m = re.search(r"\{.*\}", raw, re.S)
        obj = json.loads(m.group(0)) if m else {}
        standalone = obj.get("standalone") if isinstance(obj.get("standalone"), str) else question
        qs = [q for q in obj.get("queries", []) if isinstance(q, str) and q.strip()][:5]
        qtype = obj.get("type") if obj.get("type") in QTYPES else DEFAULT_QTYPE
        topics = [x for x in obj.get("topics", []) if x in store.TOPICS][:3]
        return standalone.strip() or question, qs, qtype, topics
    except Exception:
        return question, [], DEFAULT_QTYPE, []  # 실패해도 원 질문으로 검색은 진행


RETRIEVAL = {  # 기본 검색 설정(v6). eval/run_eval.py로 바꿔 가며 비교한다.
    "k": 8, "per_query": 5, "max_chunks": 24,            # 가이드북
    "school_k": 6, "school_per_query": 3, "max_school": 12,  # 이산중 규정
    "link_forms": True, "max_forms": 3, "form_pages": 2,   # 본문이 가리키는 부록 양식 쪽 함께 넣기
    "manual_k": 4, "manual_per_query": 2, "max_manual": 7,  # 상담 매뉴얼(문서마다)
}


def retrieve(index, question, queries, k=8, per_query=4, school_k=4, school_per_query=2, max_chunks=18, max_school=6,
             link_forms=True, max_forms=2, form_pages=2, manual_k=3, manual_per_query=1, max_manual=5, plan=None):
    """이산중 규정(1순위): 원 질문 상위 school_k개 + 검색어별 school_per_query개(최대 max_school).
    가이드북(2순위): 원 질문 상위 k개 + 검색어별 per_query개(최대 max_chunks).
    22쪽짜리 학교 규정이 216쪽 가이드북에 밀려 빠지지 않도록 문서별로 따로 뽑아 합친다.
    link_forms: 검색된 본문이 "<양식2-4>"처럼 부록 양식을 가리키면 그 양식의 앞쪽 form_pages쪽을 함께 넣는다
    (사안조사 보고서 작성 요령처럼 본문과 양식에 흩어진 내용을 모으기 위해서)."""
    texts = [question] + queries
    qvecs = embed_texts(texts) if index.emb is not None else [None] * len(texts)
    hits = []
    if plan is None:  # 유형이 없으면 학교 맞춤형 순서
        plan = [("esan", school_k, school_per_query, max_school), ("guide", k, per_query, max_chunks)]
        plan += [(d, manual_k, manual_per_query, max_manual) for d in MANUAL_DOCS]
    for doc, first, rest, cap in plan:
        best = {}
        for i, (q, qv) in enumerate(zip(texts, qvecs)):
            for h in index.search(q, k=first if i == 0 else rest, qvec=qv, doc=doc):
                h["matched_query"] = q
                if h["id"] not in best or h["cos"] > best[h["id"]]["cos"]:
                    best[h["id"]] = h
        hits += sorted(best.values(), key=lambda h: -h["score"])[:cap]
    if link_forms:
        have = {(h["doc"], h["page"]) for h in hits}
        # 여러 본문이 함께 가리키는 양식을 먼저(같으면 순위 높은 본문이 먼저 가리킨 양식)
        refs = {}
        for rank, h in enumerate(hits):
            if h["doc"] != "guide":
                continue
            for f in set(re.findall(r"<\s*양식\s*(\d+-\d+)\s*>", h["text"])):
                if f in index.form_pages:
                    cnt, first = refs.get(f, (0, rank))
                    refs[f] = (cnt + 1, min(first, rank))
        forms = sorted(refs, key=lambda f: (-refs[f][0], refs[f][1]))
        for f in forms[:max_forms]:
            for page in index.form_pages[f][:form_pages]:
                if ("guide", page) not in have:
                    have.add(("guide", page))
                    hits += index.page_hits("guide", page, qvecs[0], matched_query=f"양식{f} 연결")
    return hits


def answer(index, question, history=None, model=LLM_MODEL, temperature=0.1, expand=True, retrieval=None,
           qtype_override=None):
    """question → {'answer', 'sources', 'standalone', 'queries', 'qtype', 'doc_order', 'cited', 'flags', 'model'}"""
    standalone, queries, qtype, topics = (rewrite_query(question, history, model) if expand
                                          else (question, [], qtype_override or DEFAULT_QTYPE, []))
    flags = []
    if CRISIS_RE.search(question):
        flags.append("crisis")
        qtype = "crisis"
    if SEXUAL_RE.search(question):
        flags.append("sexual")
    if qtype_override:
        qtype = qtype_override
    spec = QTYPES[qtype]
    plan = plan_for(qtype, index, topics)
    doc_order = [d for d, *_ in plan]
    hits = retrieve(index, standalone, queries, **{**RETRIEVAL, **(retrieval or {}), "plan": plan})

    messages = [{"role": "system", "content": system_prompt()}]
    for m in (history or [])[-4:]:
        messages.append({"role": m["role"], "content": m["content"]})
    note = ""
    if "crisis" in flags:
        note += "\n(참고: 질문에 자해·자살 위험 신호가 있음 → 규칙 10 적용)"
    if "sexual" in flags:
        note += "\n(참고: 성 관련 사안 가능성 → 규칙 9 적용)"
    order_text = " → ".join(f"{i + 1}순위 [{DOCS[d]['short']}]" for i, d in enumerate(doc_order))
    messages.append({"role": "user", "content": f"[자료]\n{format_context(hits, doc_order)}\n\n"
                                                f"[질문 유형] {spec['label']}\n[확인 순서] {order_text}\n"
                                                + "".join(f"[추가 자료] [{DOCS[d]['short']}] {DOCS[d]['name']}: {DOCS[d].get('summary', '')}\n"
                                                          for d in doc_order if d in added_docs())
                                                + f"[답변 형식]\n{spec['format']}\n\n[질문]\n{question}{note}\n\n"
                                                "(이번 질문의 근거는 위 [자료]에서만 찾는다. 이전 답변의 쪽 번호를 재사용하지 않는다.)"})

    resp = client().chat.completions.create(model=model, temperature=temperature, messages=messages)
    text = resp.choices[0].message.content or ""
    cited = parse_citations(text)
    hit_keys = {(h["doc"], h["page"]) for h in hits}
    for h in hits:
        h["cited"] = (h["doc"], h["page"]) in cited
    return {
        "answer": text,
        "sources": hits,
        "standalone": standalone,
        "queries": queries,
        "qtype": qtype,
        "topics": topics,
        "doc_order": doc_order,
        "cited": cited,
        "uncited_ok": [c for c in cited if c not in hit_keys],  # 검색 자료에 없는 쪽을 인용했으면 확인 필요
        "flags": flags,
        "model": model,
    }


def log_turn(question, result):
    import time
    LOG_PATH.parent.mkdir(exist_ok=True)
    rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "question": question, "answer": result["answer"],
           "model": result["model"], "flags": result["flags"], "qtype": result.get("qtype"), "topics": result.get("topics"),
           "doc_order": result.get("doc_order"), "standalone": result["standalone"],
           "queries": result["queries"], "cited": [cite_label(d, p) for d, p in result["cited"]],
           "sources": [{"id": h["id"], "doc": h["doc"], "page": h["page"], "section": h["section"], "cos": round(h["cos"], 4),
                        "bm25": round(h["bm25"], 2), "rrf": round(h["score"], 5), "cited": h["cited"],
                        "matched_query": h.get("matched_query", ""), "text": h["text"]}
                       for h in result["sources"]]}
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------- 전체 문서 모드(비교 기준선) ----------
_full_pages = None


def full_pages():
    """두 문서의 모든 쪽 텍스트(청크 아님). Gemini 노트북처럼 문서 전체를 넣는 비교 기준선에 쓴다."""
    global _full_pages
    if _full_pages is None:
        import pymupdf
        _full_pages = []
        for doc_id, meta in DOCS.items():
            for i, page in enumerate(pymupdf.open(ROOT / "data" / meta["file"])):
                text = clean_page_text(page.get_text())
                if len(text) >= 30:
                    _full_pages.append({"doc": doc_id, "page": i + 1, "text": text})
    return _full_pages


def answer_full(question, model=LLM_MODEL, temperature=0.1):
    """검색 없이 두 문서 전체를 넣고 답한다(이산중 규정 먼저)."""
    pages = sorted(full_pages(), key=lambda x: (x["doc"] != "esan", x["page"]))
    context = "\n\n".join(f"<<{cite_label(p['doc'], p['page'])}>>\n{p['text']}" for p in pages)
    resp = client().chat.completions.create(model=model, temperature=temperature, messages=[
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": f"[자료]\n{context}\n\n[질문]\n{question}\n\n(근거는 위 [자료]에서만 찾는다.)"}])
    text = resp.choices[0].message.content or ""
    cited = parse_citations(text)
    sources = [{"doc": p["doc"], "page": p["page"]} for p in pages]
    return {"answer": text, "sources": sources, "cited": cited, "standalone": question, "queries": [],
            "uncited_ok": [], "flags": [], "model": model}


refresh_docs()
