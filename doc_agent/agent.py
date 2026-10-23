"""참고자료 타당성 평가 에이전트.

새 PDF를 근거 자료로 넣어도 되는지 criteria.json의 12개 기준으로 판단해 추천 / 조건부 / 비추천을 낸다.

1단계(자동 측정, Python): 쪽 수, 텍스트 추출 품질, 연도·발행처 후보, 개인정보 패턴, 지역, 주제 키워드,
                         기존 자료와의 중복(임베딩 유사도·제목 비교)
2단계(Gemini 판단): 자동 측정값 + 표지·목차·본문 표본 쪽을 읽고 기준마다 점수·근거(쪽 번호)를 매김
3단계(최종 판정, Python): criteria.json의 decision 규칙으로 추천 / 조건부 / 비추천 결정(필수 기준은 코드로 강제)

실행:  C:\\Users\\2003g\\anaconda3\\envs\\DS\\python.exe doc_agent\\agent.py <PDF 경로>
결과:  doc_agent/reports/<파일이름>.json
"""
import difflib
import json
import re
import sys
import time
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
ROOT = AGENT_DIR.parent
sys.path.insert(0, str(ROOT))

import rag  # noqa: E402

CRITERIA = json.loads((AGENT_DIR / "criteria.json").read_text(encoding="utf-8"))
REPORT_DIR = AGENT_DIR / "reports"

REGIONS = ["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원", "충북", "충남",
           "전북", "전남", "경북", "경남", "제주"]
TOPICS = {
    "학교폭력": r"학교폭력|따돌림|괴롭힘|사이버폭력",
    "가정": r"아동학대|가정폭력|방임|보호자 학대",
    "학업·학교부적응": r"학업중단|학업 중단|학교부적응|학교 부적응|기초학력|등교거부",
    "정서·위기": r"자해|자살|우울|불안|위기개입",
    "디지털·사이버": r"사이버|디지털|스마트폰|SNS|온라인",
    "상담 기법": r"상담개입|상담 개입|회기|개인상담|집단상담|라포|공감",
    "학교 운영": r"Wee 클래스|위\(Wee\)|학생생활규정|학칙|생활교육위원회",
}
PII = {
    "휴대전화": r"01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}",
    "주민번호": r"\d{6}[-\s]?[1-4]\d{6}",
    "이메일": r"[\w.+-]+@[\w-]+\.[\w.]+",
}


# ---------- 1단계: 자동 측정 ----------
def measure(pdf_path, index=None, exclude_doc=None):
    import numpy as np
    import pymupdf
    doc = pymupdf.open(pdf_path)
    pages = rag.page_texts(pdf_path)  # 글자층이 없는 쪽은 OCR 결과로
    n = len(pages)
    lens = [len(t) for t in pages]
    low = [i + 1 for i, L in enumerate(lens) if L < 80]
    full = "\n".join(pages)
    head = "\n".join(pages[:6])

    years = sorted({int(y) for y in re.findall(r"(20[0-3]\d|19[89]\d)\s*(?:년|학년도|\.|\s*년도)", head)})
    meta = doc.metadata or {}
    publishers = sorted(set(re.findall(
        r"(교육부|[가-힣]{2,4}(?:특별시|광역시|특별자치시|특별자치도|도)?교육청|한국청소년상담복지개발원|한국교육개발원|"
        r"국가아동권리보장원|한국청소년정책연구원|이화여자대학교|[가-힣]+학회|[가-힣]+연구소|[가-힣]+대학교)", head)))
    region_hits = {r: len(re.findall(r, head)) for r in REGIONS}
    region_hits = {r: c for r, c in region_hits.items() if c}
    topic_hits = {k: len(re.findall(v, full)) for k, v in TOPICS.items()}
    pii_hits = {k: len(re.findall(v, full)) for k, v in PII.items()}
    masked = len(re.findall(r"[가-힣]○○|○○[가-힣]|[가-힣]OO\b", full))
    restriction = sorted(set(re.findall(r"무단\s*(?:전재|복제|배포)[^\n]{0,30}|재배포[^\n]{0,20}금지|저작권[^\n]{0,30}", full)))[:5]

    # 기존 자료와 비교: 제목 유사도 + 본문 표본 임베딩 최대 코사인
    title = " ".join(pages[0].split())[:120] if pages else ""
    title_sim = {}
    for d, m in rag.DOCS.items():
        if d == exclude_doc:
            continue
        title_sim[d] = round(difflib.SequenceMatcher(None, Path(pdf_path).stem, Path(m["file"]).stem).ratio(), 3)
    dup = {}
    if index is not None and index.emb is not None:
        sample_idx = [i for i in np.linspace(0, n - 1, min(n, 10)).astype(int) if lens[i] > 300]
        samples = [pages[i][:1500] for i in sample_idx]
        if samples:
            vecs = rag.embed_texts(samples)
            keep = np.array([c["doc"] != exclude_doc for c in index.chunks])  # 정기 점검 때 자기 자신은 뺀다
            ref_emb, ref_chunks = index.emb[keep], [c for c, k in zip(index.chunks, keep) if k]
            sims = vecs @ ref_emb.T  # (표본, 기존 청크)
            best = sims.max(axis=1)
            best_doc = [ref_chunks[j]["doc"] for j in sims.argmax(axis=1)]
            for d in set(best_doc):
                vals = [float(b) for b, bd in zip(best, best_doc) if bd == d]
                dup[d] = {"samples": len(vals), "max_cos": round(max(vals), 3), "mean_cos": round(sum(vals) / len(vals), 3)}
    return {
        "file": Path(pdf_path).name, "pages": n, "chars_total": sum(lens),
        "ocr": ({k: v for k, v in (rag.ocr_cache(pdf_path) or {}).items() if k != "pages"} or None),
        "chars_per_page_median": sorted(lens)[n // 2] if n else 0,
        "low_text_pages_ratio": round(len(low) / n, 3) if n else 1.0, "low_text_pages": low[:30],
        "years_on_cover": years, "pdf_metadata_date": (meta.get("creationDate") or "")[2:10],
        "publisher_candidates": publishers, "region_mentions_on_cover": region_hits,
        "topic_keyword_counts": topic_hits, "pii_pattern_counts": pii_hits, "masked_name_count": masked,
        "restriction_phrases": restriction, "first_page_text": title,
        "filename_similarity_to_existing": title_sim, "content_overlap_with_existing": dup,
    }, pages


def sample_pages(pages, max_chars=26000):
    """표지·목차(앞 5쪽) + 본문에서 고르게 뽑은 쪽 + 끝 2쪽(발행 정보)."""
    n = len(pages)
    picks = list(range(min(5, n)))
    step = max(1, (n - 7) // 10)
    picks += list(range(5, max(5, n - 2), step))[:10]
    picks += [i for i in range(max(0, n - 2), n)]
    out, total = [], 0
    for i in sorted(set(picks)):
        block = f"<<p.{i + 1}>>\n{pages[i][:2200]}"
        if total + len(block) > max_chars:
            break
        out.append(block)
        total += len(block)
    return "\n\n".join(out)


# ---------- 2단계: Gemini 판단 ----------
JUDGE_PROMPT = """너는 학교 전문상담교사가 쓰는 AI 도우미의 '참고자료 심사관'이다.
새 PDF를 근거 자료로 넣어도 되는지 [기준]에 따라 엄격하게 판단한다. 상담사가 믿고 따를 정보이므로, 확인되지 않는 것은 좋게 보지 않는다.

[서비스 정보]
- 오늘 날짜: {today} (최신성은 오늘 기준으로 판단한다)
- 학교: {school} ({school_info})
- 이미 등록된 자료:
{existing}

[기준]
{criteria}

[규칙]
- 기준마다 score(0/1/2)와 evidence(무엇을 보고 판단했는지, 가능하면 쪽 번호 p.N 포함)를 쓴다. 'topic' 기준은 score를 null로 두고 evidence에 분류를 쓴다.
- 자동 측정값과 표본 쪽에서 확인되는 사실만 근거로 쓴다. 확인할 수 없으면 그렇게 쓰고 점수를 낮게 준다.
- 자동 측정값의 ocr이 있으면 본문은 PDF 글자층이 아니라 OCR로 읽은 글자다. 'extraction'(텍스트 추출 품질)은 OCR 결과가 읽을 만한지(오탈자·빠진 쪽)로 판단하고, 원본이 글자층 없는 PDF라는 점을 evidence에 적는다. 'cautions'에 'OCR로 읽은 자료이므로 숫자·표는 원본 쪽 이미지로 확인'을 넣는다.
- 'duplicate'는 자동 측정한 content_overlap_with_existing(기존 자료 표본 최대 코사인 0.93 이상이면 거의 같은 내용)과 제목·연도를 함께 본다. 등록된 자료의 구버전이면 0점.
- 추가로 아래도 제안한다:
  - short: 답변 인용에 쓸 짧은 이름(2~6자, 기존 이름과 겹치지 않게)
  - title: 정식 이름(발행 기관, 연도 포함)
  - role: "procedure"(학교폭력 절차·법 지침), "school"(이 학교 규정), "manual"(상담 기법·개입 매뉴얼), "reference"(기타 참고) 중 하나
  - use_in: 이 자료를 확인할 질문 유형 목록(school, procedure, definition, counseling, student_explain, crisis 중. crisis는 자해·자살 위기 대응을 다루는 자료일 때만)
  - topics: 주제 분류 목록
  - region: 이 자료가 적용되는 지역. "전국"(교육부·전국 공통), "경기"(경기도교육청), "화성·오산"(화성오산교육지원청), "학교"(한 학교 규정), 그 밖의 시·도면 그 이름(예: "서울")
  - summary: 자료가 무엇이고 상담사에게 어떤 도움이 되는지 2~3문장
  - cautions: 추가한다면 주의할 점 목록
JSON 객체만 출력한다:
{{"scores": {{"recency": {{"score": 2, "evidence": "..."}}, ...}}, "short": "...", "title": "...", "role": "...",
 "use_in": [...], "topics": [...], "region": "...", "summary": "...", "cautions": [...]}}"""


def judge(measured, pages, model=rag.LLM_MODEL, exclude_doc=None):
    existing = "\n".join(f"  - [{m['short']}] {m['name']} (파일: {m['file']})"
                         for d, m in rag.DOCS.items() if d != exclude_doc) or "  (없음)"
    crit = "\n".join(f"- {c['id']} | {c['name']}: {c['check']} 채점: {c['rule']}" for c in CRITERIA["criteria"])
    system = JUDGE_PROMPT.format(today=time.strftime("%Y-%m-%d"), school=rag.SCHOOL_NAME, school_info=rag.SCHOOL_INFO, existing=existing, criteria=crit)
    user = (f"[자동 측정값]\n{json.dumps(measured, ensure_ascii=False, indent=1)}\n\n"
            f"[표본 쪽]\n{sample_pages(pages)}")
    resp = rag.client().chat.completions.create(model=model, temperature=0,
                                                messages=[{"role": "system", "content": system},
                                                          {"role": "user", "content": user}])
    raw = resp.choices[0].message.content or ""
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0))


# ---------- 3단계: 최종 판정 ----------
def decide(scores):
    """criteria.json decision 규칙을 코드로 적용한다."""
    crit = CRITERIA["criteria"]
    must = [c["id"] for c in crit if c.get("must_pass")]
    core = ["recency", "source", "authority", "usefulness"]
    got = {c["id"]: (scores.get(c["id"]) or {}).get("score") for c in crit}
    reasons = []
    # 점수가 빠진 기준은 0점으로 본다(확인하지 못한 것은 좋게 보지 않는다)
    missing = [c["name"] for c in crit if c["weight"] and not isinstance(got[c["id"]], int)]
    for c in crit:
        if c["weight"] and not isinstance(got[c["id"]], int):
            got[c["id"]] = 0
    if missing:
        reasons.append("판단 누락(0점 처리): " + ", ".join(missing))
    weighted = [(c["weight"], got[c["id"]]) for c in crit if c["weight"]]
    pct = round(100 * sum(w * s for w, s in weighted) / max(1, sum(2 * w for w, _ in weighted)), 1)
    failed_must = [cid for cid in must if not isinstance(got.get(cid), int) or got[cid] == 0]
    if failed_must:
        names = [c["name"] for c in crit if c["id"] in failed_must]
        reasons.append("필수 기준 미달: " + ", ".join(names))
        return "비추천", pct, reasons
    if pct < 55:
        reasons.append(f"가중 점수 {pct}% < 55%")
        return "비추천", pct, reasons
    zero_any = [c["name"] for c in crit if c["weight"] and got.get(c["id"]) == 0]
    if pct >= 75 and not zero_any:
        return "추천", pct, reasons + [f"가중 점수 {pct}% ≥ 75%, 필수 기준 통과, 0점 기준 없음"]
    if zero_any:
        reasons.append("0점 기준이 있어 추천 불가: " + ", ".join(zero_any))
    if pct < 75:
        reasons.append(f"가중 점수 {pct}% (55~75%)")
    return "조건부", pct, reasons


def evaluate_pdf(pdf_path, index=None, save=True, exclude_doc=None):
    """exclude_doc: 이미 등록된 자료를 다시 점검할 때 그 자료 ID(중복 비교에서 자기 자신을 뺀다)."""
    t = time.time()
    if index is None:
        index = rag.Index()
    measured, pages = measure(pdf_path, index, exclude_doc)
    j = judge(measured, pages, exclude_doc=exclude_doc)
    scores = j.get("scores", {})
    verdict, pct, reasons = decide(scores)
    short = (j.get("short") or Path(pdf_path).stem[:6]).strip()
    taken = {m["short"] for d, m in rag.DOCS.items() if d != exclude_doc}
    if short in taken:
        short = short + "2"
    report = {
        "file": Path(pdf_path).name, "evaluated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "model": rag.LLM_MODEL,
        "criteria_version": CRITERIA["version"], "verdict": verdict, "weighted_score": pct, "reasons": reasons,
        "scores": {c["id"]: {"name": c["name"], "weight": c["weight"], "must_pass": c.get("must_pass", False),
                             **(scores.get(c["id"]) or {"score": None, "evidence": "판단 결과 없음"})}
                   for c in CRITERIA["criteria"]},
        "proposal": {"short": short, "title": j.get("title") or Path(pdf_path).stem, "role": j.get("role", "reference"),
                     "use_in": [u for u in j.get("use_in", []) if u in rag.QTYPES],
                     "topics": j.get("topics", []), "region": j.get("region"),
                     "summary": j.get("summary", ""), "cautions": j.get("cautions", [])},
        "measured": measured, "seconds": round(time.time() - t, 1),
    }
    if save:
        REPORT_DIR.mkdir(exist_ok=True)
        (REPORT_DIR / f"{Path(pdf_path).stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                                                 encoding="utf-8")
    return report


def print_report(r):
    print(f"\n===== {r['file']}  →  {r['verdict']}  (가중 점수 {r['weighted_score']}%, {r['seconds']}초)")
    for reason in r["reasons"]:
        print("  사유:", reason)
    for cid, s in r["scores"].items():
        mark = "★" if s["must_pass"] else " "
        print(f"  {mark}{s['name']:<18} {s.get('score')}  {str(s.get('evidence'))[:110]}")
    p = r["proposal"]
    print(f"  제안: [{p['short']}] {p['title']} | 역할 {p['role']} | 질문 유형 {p['use_in']} | 주제 {p['topics']}")
    for c in p["cautions"][:4]:
        print("  주의:", c)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ix = rag.Index()
    for path in sys.argv[1:]:
        print_report(evaluate_pdf(path, ix))
