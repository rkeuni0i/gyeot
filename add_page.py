"""'📚 자료 관리' 화면: 자료 추가(평가·승인) / 등록 자료(사용 중지·완전 삭제). 알림·정기 점검은 render_alerts(사이드바 🔔 아이콘)."""
import json
from pathlib import Path

import pandas as pd
import streamlit as st

import rag
import store
import ui_style
from doc_agent import agent, dedupe, ingest, ocr, periodic_review

INBOX = Path(__file__).resolve().parent / "doc_agent" / "inbox"
VERDICT_STYLE = {"추천": st.success, "조건부": st.warning, "비추천": st.error}
TYPE_LABELS = {k: v["label"] for k, v in rag.QTYPES.items()}
LAYER_LABELS = {"common": "공통(모든 질문)", "topic": "주제(주제가 겹칠 때)", "school": "학교(이 학교 자료)"}
SEVERITY = {"critical": ("🔴", st.error), "warn": ("🟠", st.warning), "info": ("🔵", st.info)}


def flash(msg):
    st.session_state.manage_msg = msg
    st.cache_resource.clear()
    st.rerun()


# ---------- 추가 ----------
def show_criteria():
    with st.expander("평가 기준 12개 보기 (doc_agent/criteria.json)"):
        rows = [{"기준": c["name"], "가중치": c["weight"], "필수": "★" if c.get("must_pass") else "",
                 "확인할 것": c["check"], "채점": c["rule"]} for c in agent.CRITERIA["criteria"]]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        d = agent.CRITERIA["decision"]
        st.markdown(f"- **추천**: {d['recommend']}\n- **조건부**: {d['conditional']}\n- **비추천**: {d['not_recommended']}")


def show_report(r):
    VERDICT_STYLE[r["verdict"]](f"**AI 판정: {r['verdict']}** · 가중 점수 {r['weighted_score']}% · "
                                + " / ".join(r["reasons"]))
    rows = [{"기준": s["name"], "필수": "★" if s["must_pass"] else "",
             "점수": "-" if s.get("score") is None else f"{s['score']} / 2", "근거": s.get("evidence", "")}
            for s in r["scores"].values()]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                 column_config={"근거": st.column_config.TextColumn(width="large")})
    p = r["proposal"]
    st.markdown(f"**요약**: {p['summary']}")
    if p["cautions"]:
        st.markdown("**주의할 점** (등록하면 '사용 전 알림'으로 남습니다)\n" + "\n".join(f"- {c}" for c in p["cautions"]))
    m = r["measured"]
    with st.expander("자동 측정값"):
        st.markdown(
            f"- 쪽 수 {m['pages']} · 쪽당 글자 수(중앙값) {m['chars_per_page_median']} · "
            f"글자가 거의 없는 쪽 비율 {m['low_text_pages_ratio']:.0%}\n"
            f"- 표지 연도 {m['years_on_cover']} · 발행처 후보 {m['publisher_candidates']} · 지역 언급 {m['region_mentions_on_cover']}\n"
            f"- 개인정보 패턴 {m['pii_pattern_counts']} · 가려진 이름(○○) {m['masked_name_count']}회 · 이용 제한 문구 {m['restriction_phrases']}\n"
            f"- 기존 자료와 본문 유사도 {m['content_overlap_with_existing']}")


def used_shorts():
    """저장소에 있는 모든 인용 이름 → 정식 이름(사용 중지 자료 포함)."""
    con = store.connect()
    rows = dict(con.execute("SELECT short, title FROM documents").fetchall())
    con.close()
    return rows


def free_short(name, taken, year=None):
    """겹치지 않는 인용 이름을 만든다: 이름 → 이름+연도 → 이름2, 이름3 …"""
    if name not in taken:
        return name
    if year and f"{name}{year}" not in taken:
        return f"{name}{year}"
    i = 2
    while f"{name}{i}" in taken:
        i += 1
    return f"{name}{i}"


EDITION_MODES = {"replace": "이전 판을 이 판으로 교체 (이전 판 PDF·청크 삭제)",
                 "inactive": "이전 판은 사용 중지하고, 이 판을 새 자료로 추가",
                 "keep": "둘 다 그대로 두고, 이 판을 새 자료로 추가"}


def edition_choice(path):
    """개정판이면 이전 판을 어떻게 할지 고르게 한다. (모드, 이전 판 정보, 확인 여부)"""
    dd = st.session_state.get("dedupe", {})
    cands = [m for m in dd.get("res", []) if dd.get("path") == path
             and (m["relation"] == "newer" or (m["relation"] == "overlap" and max(m["new_in_old"], m["old_in_new"]) >= dedupe.EDITION_MIN))]
    if not cands:
        return "new", None, True
    old = cands[0]
    st.markdown("##### 이전 판 처리")
    st.info(f"이 PDF는 등록된 **[{old['short']}]** {old['title']} · {old['old_year'] or '연도 모름'}년 판의 "
            f"{'개정판' if old['relation'] == 'newer' else '다른 판'}으로 보입니다. {old['why']} "
            f"(본문 겹침: 새 자료의 {old['new_in_old']:.0%}가 이전 판에 있음, 이전 판의 {old['old_in_new']:.0%}가 새 자료에 있음)")
    modes = list(EDITION_MODES)
    mode = st.radio("이전 판을 어떻게 할까요?", modes, index=0 if old["relation"] == "newer" else 2,
                    format_func=EDITION_MODES.get, key="add_edition_mode")
    ok = True
    if mode == "replace":
        st.markdown(f"- **유지**: 인용 이름 [{old['short']}], 질문 유형별 확인 순서, 이전 평가 기록(이력)\n"
                    f"- **삭제**: 이전 판 PDF 파일, 이전 판 청크, 이전 판에 대해 열려 있던 알림\n"
                    f"- 답변에는 앞으로 새 판 쪽 번호로 [{old['short']} p.쪽]이 달립니다.")
        ok = st.checkbox(f"이전 판 [{old['short']}] · {old['old_year'] or '?'}년 판을 삭제하고 이 판으로 바꾸는 데 동의합니다.",
                         key="add_edition_ok")
    elif mode == "inactive":
        st.caption(f"[{old['short']}]은 검색에서만 빠지고 파일·청크는 남습니다. 자료 관리 → 등록 자료에서 다시 쓰거나 완전 삭제할 수 있습니다.")
    return mode, old, ok


def slot_choice(r):
    """완전 삭제돼 비어 있는 기본 자료 자리가 있으면 그 자리로 등록할지 고르게 한다. 고른 자리 ID 또는 None."""
    slots = ingest.empty_base_slots()
    if not slots:
        return None
    import difflib
    first = r["proposal"]["title"] + " " + r["measured"].get("first_page_text", "")
    sim = {d: difflib.SequenceMatcher(None, dedupe._norm_title(first)[:60], dedupe._norm_title(rag.BASE_SEED[d]["name"])).ratio()
           for d in slots}
    best = max(slots, key=sim.get)
    options = [None] + slots
    st.markdown("##### 등록 위치")
    pick = st.radio("비어 있는 기본 자료 자리가 있습니다. 어디에 등록할까요?", options,
                    index=options.index(best) if sim[best] >= 0.5 else 0,
                    format_func=lambda d: "새 추가 자료로 등록" if d is None else
                    f"기본 자료 자리 [{rag.BASE_SEED[d]['short']}] (원래: {rag.BASE_SEED[d]['name']}) · 제목 유사도 {sim[d]:.0%}",
                    key="add_slot")
    if pick:
        st.caption(f"[{rag.BASE_SEED[pick]['short']}] 이름으로 들어가 질문 유형별 확인 순서에서 원래 자리를 차지합니다"
                   + (" (가이드북 양식 연결 포함)." if pick == "guide" else "."))
    return pick


def add_form(r, path):
    p = r["proposal"]
    mode, old, edition_ok = edition_choice(path)
    slot = slot_choice(r) if mode == "new" else None
    if slot:
        mode = "slot"
    st.markdown("##### 등록 정보")
    taken = used_shorts()
    years = r.get("measured", {}).get("years_on_cover") or []
    c1, c2 = st.columns([1, 2])
    if mode in ("replace", "slot"):
        short = old["short"] if mode == "replace" else rag.BASE_SEED[slot]["short"]
        c1.text_input("인용 이름 (이전 판 이름 유지)" if mode == "replace" else "인용 이름 (기본 자료 이름)",
                      value=short, disabled=True, key=f"add_short_fixed_{mode}")
        clash = False
    else:
        short = c1.text_input("인용 이름 (답변에 [이름 p.쪽]으로 표시)", value=free_short(p["short"], taken, max(years, default=None)),
                              key="add_short", help="짧은 별칭(2~8자). 다른 자료와 겹치면 안 됩니다. 정식 이름은 근거 표와 자료 목록에 함께 보입니다.")
        clash = short.strip() in taken
        if clash:
            c1.error(f"'{short.strip()}'은 이미 [{taken[short.strip()]}]에 쓰고 있습니다. 예: {free_short(short.strip(), taken, max(years, default=None))}")
    title = c2.text_input("정식 이름", value=p["title"], key="add_title")
    c3, c4 = st.columns(2)
    if mode == "replace":
        con = store.connect()
        old_meta = store.documents(con)[old["doc_id"]]
        con.close()
        default_layer, default_topics, default_use = old_meta["layer"], old_meta["topics"], old_meta["qtypes"]
    elif mode == "slot":
        bm = rag.BASE_META[slot]
        default_layer, default_topics = bm["layer"], bm["topics"]
        default_use = [q for q, spec in rag.QTYPES.items() if any(x[0] == slot for x in spec["plan"])]
    else:
        default_layer = ingest.LAYER_BY_ROLE.get(p.get("role"), "topic")
        default_topics = ingest._map_topics(p.get("topics", []))
        default_use = [u for u in p["use_in"] if u in TYPE_LABELS]
    layer = c3.selectbox("층", list(LAYER_LABELS), index=list(LAYER_LABELS).index(default_layer),
                         format_func=LAYER_LABELS.get, key=f"add_layer_{mode}")
    topics = c4.multiselect("주제", store.TOPICS, default=[t for t in default_topics if t in store.TOPICS], key=f"add_topics_{mode}")
    use_in = st.multiselect("이 자료를 확인할 질문 유형", options=list(TYPE_LABELS),
                            default=[u for u in default_use if u in TYPE_LABELS],
                            format_func=lambda k: TYPE_LABELS[k], key=f"add_use_in_{mode}")
    verdict = r["verdict"]
    note, ok = "", edition_ok
    if verdict == "조건부":
        ok = st.checkbox("주의할 점을 확인했고, 그래도 추가합니다.", key="add_ack") and ok
    elif verdict == "비추천":
        ack = st.checkbox("AI는 비추천했지만 상담사 판단으로 추가합니다.", key="add_ack")
        note = st.text_input("추가하는 이유 (기록에 남습니다)", key="add_note")
        ok = ok and ack and bool(note.strip())
    b1, b2 = st.columns(2)
    label = {"추천": "추천대로 추가", "조건부": "확인 후 추가", "비추천": "그래도 추가"}[verdict]
    if mode == "replace":
        label = f"[{old['short']}] 새 판으로 교체"
    if b1.button(label, type="primary", disabled=not (ok and use_in and short.strip()) or clash, use_container_width=True):
        # 등록(1분 안팎) 중에 버튼을 한 번 더 누르면 두 번째 요청이 들어온다. 같은 PDF가 이미 들어가 있으면 완료로 본다.
        same = ingest.find_same_file(Path(path))
        if same:
            finish_add({"title": "이미 등록되어 있습니다", "short": rag.DOCS.get(same, {}).get("short", same),
                        "lines": ["같은 PDF가 저장소에 들어 있어 다시 등록하지 않았습니다. 챗봇에서 바로 검색됩니다."]})
        with st.spinner("저장소에 등록하고 청크를 임베딩하는 중… (1분 안팎) 버튼을 다시 누르지 마세요."):
            try:
                if mode == "replace":
                    r["dedupe_year"] = old.get("new_year")
                    n, removed = ingest.replace_edition(old["doc_id"], path, r, title=title, use_in=use_in,
                                                        topics=topics, layer=layer, note=note)
                    done = {"title": "새 판으로 교체 완료", "short": short, "n": n, "lines": [
                        f"이전 판({old['old_year'] or '?'}) → 새 판({old.get('new_year') or '?'})으로 바꿨습니다.",
                        "삭제한 이전 판 파일: " + (", ".join(removed) or "없음"),
                        "🔔 알림에 '개정판으로 교체됨'이 남았습니다. 바뀐 기한·절차를 한 번 확인하세요."]}
                else:
                    doc_id, n = ingest.add_document(path, r, short=short, title=title, use_in=use_in, topics=topics,
                                                    layer=layer, note=note, base_slot=slot)
                    done = {"title": "등록 완료", "short": short, "n": n, "lines": [f"정식 이름: {title}"]}
                    if slot:
                        done["lines"].append(f"기본 자료 자리 [{short}]에 들어가 질문 유형별 확인 순서에서 원래 자리를 차지합니다.")
                    if mode == "inactive":
                        ingest.set_status(old["doc_id"], "inactive")
                        done["lines"].append(f"이전 판 [{old['short']}]은 사용 중지했습니다.")
            except ValueError as e:
                st.error(str(e))
                return
        finish_add(done)
    if b2.button("추가하지 않음", use_container_width=True):
        finish_add({"title": "추가하지 않았습니다", "short": None,
                    "lines": ["평가 결과는 doc_agent/reports/에 남아 있습니다."]})




STEPS = ["PDF 파일 올리기", "중복·판 확인", "AI 평가", "등록·승인"]
REL_STYLE = {"same": ("🟥", "이미 등록된 자료"), "newer": ("🟦", "개정판(새 판)"),
             "older": ("🟧", "구버전(오래된 판)"), "overlap": ("⬜", "일부 겹침")}


def step_bar(current):
    """자료 추가 단계 표시: 지난 단계 ✓, 지금 단계 강조."""
    cells = []
    for i, name in enumerate(STEPS, 1):
        if i < current:
            style, mark = f"background:{ui_style.PILL_DONE};color:{ui_style.INK};", "✓ "
        elif i == current:
            style, mark = f"background:{ui_style.PILL_ACTIVE};color:{ui_style.INK};font-weight:700;", ""
        else:
            style, mark = f"background:{ui_style.PILL};color:{ui_style.MUTED};", ""
        cells.append(f'<div style="flex:1 1 8em;padding:.75em 1em;border-radius:18px;white-space:nowrap;'
                     f'text-align:center;font-size:.95em;{style}">{mark}{name}</div>')
    st.markdown('<div style="display:flex;gap:.5em;margin:.2em 0 1em;flex-wrap:wrap">' + "".join(cells) + "</div>",
                unsafe_allow_html=True)


def show_dedupe(path, up):
    """올린 PDF를 등록된 자료와 본문으로 비교해 보여 준다(파일 이름은 보지 않음). 같은 자료면 그 자료를 돌려준다."""
    key = (up.name, up.size)
    dd = st.session_state.get("dedupe")
    if not dd or dd.get("key") != key:
        with st.spinner("등록된 자료와 본문을 비교하는 중… (몇 초)"):
            dd = {"key": key, "path": path, "res": dedupe.compare(path)}
        st.session_state.dedupe = dd
    res = dd["res"]
    st.markdown("#### 2. 중복·판 확인")
    st.caption("파일 이름이 아니라 본문 문장(단어 4개 묶음, 숫자·연도 제외)이 얼마나 겹치는지와 발행 연도로 판단합니다.")
    if not res:
        st.success("✅ 새 자료입니다. 등록된 자료와 겹치는 본문이 없습니다.")
        return None
    top = res[0]
    icon, label = REL_STYLE[top["relation"]]
    with st.container(border=True):
        st.markdown(f"**{icon} {label}** · 비교 대상: **[{top['short']}]** {top['title']}")
        ny, oy = top.get("new_year_info") or {}, top.get("old_year_info") or {}
        boxes = [("발행 연도 (올린 → 등록)", f"{top['new_year'] or '?'} → {top['old_year'] or '?'}",
                  f"올린: {ny.get('source', '?')} · 등록: {oy.get('source', '?')}"),
                 ("올린 자료 중 겹치는 본문", f"{top['new_in_old']:.0%}", ""),
                 ("등록 자료 중 겹치는 본문", f"{top['old_in_new']:.0%}", "")]
        st.markdown('<div style="display:flex;gap:.6em;flex-wrap:wrap;margin:.3em 0 .6em">' + "".join(
            f'<div style="flex:1 1 9em;padding:.5em .8em;border-radius:10px;background:rgba(128,128,128,.08)">'
            f'<div style="font-size:.8em;opacity:.75">{k}</div><div style="font-size:1.35em;font-weight:700">{v}</div>'
            + (f'<div style="font-size:.78em;opacity:.8">{sub}</div>' if sub else "") + "</div>"
            for k, v, sub in boxes) + "</div>", unsafe_allow_html=True)
        # 연도를 어디서 읽었는지: PDF 글자(표지·발행 정보·본문)면 문맥을, 파일 이름·제목·등록 정보면 확인 요청
        for who, yi in (("올린 자료", ny), ("등록 자료", oy)):
            if not yi:
                continue
            if yi.get("weak"):
                st.warning(f"⚠️ {who}의 연도({yi.get('year') or '?'})는 PDF 글자가 아니라 **{yi.get('source')}**에서 읽었습니다"
                           + (f" ({yi['detail']})" if yi.get("detail") else "") + ". 맞는 판인지 한 번 확인하세요.")
            else:
                st.caption(f"{who} 연도 근거 · {yi['source']}: “{yi['detail'][:80]}”")
        msg = {
            "same": "같은 자료라 추가할 필요가 없습니다. AI 평가를 막았습니다.",
            "newer": "등록된 자료의 새 판입니다. AI 평가 후 '이전 판 처리'에서 이전 판을 이 판으로 교체할 수 있습니다(삭제 전 확인).",
            "older": "등록된 자료보다 오래된 판입니다. 추가하지 않는 것을 권합니다.",
            "overlap": ("같은 자료의 다른 판일 수 있지만 연도로 구분하지 못했습니다. 위 연도를 확인하고, AI 평가 후 '이전 판 처리'에서 직접 고르세요."
                        if max(top["new_in_old"], top["old_in_new"]) >= dedupe.EDITION_MIN else
                        "본문 일부만 겹칩니다. 다른 자료로 보고 추가해도 됩니다."),
        }[top["relation"]]
        (st.error if top["relation"] == "same" else st.warning if top["relation"] == "older" else st.info)(
            f"{msg} 판단 근거: {top['why']}.")
    if len(res) > 1:
        with st.expander(f"다른 비교 결과 {len(res) - 1}건"):
            st.dataframe(pd.DataFrame([{
                "등록 자료": f"[{m['short']}] {m['title']}", "판단": REL_STYLE[m["relation"]][1],
                "올린 자료 중 겹침": f"{m['new_in_old']:.0%}", "등록 자료 중 겹침": f"{m['old_in_new']:.0%}",
                "연도(올린/등록)": f"{m['new_year'] or '?'} / {m['old_year'] or '?'}"} for m in res[1:]]),
                hide_index=True, use_container_width=True)
    return top if top["relation"] == "same" else None


def finish_add(done):
    """등록(또는 취소)이 끝나면: 평가·비교 결과를 지우고, 업로드 칸을 비우고, 완료 화면을 보여 준다."""
    st.session_state.pop("eval_report", None)
    st.session_state.pop("dedupe", None)
    st.session_state.upload_gen = st.session_state.get("upload_gen", 0) + 1  # 업로드 칸 새로 만들기(올린 파일 비움)
    st.session_state.add_done = done
    st.cache_resource.clear()
    st.rerun()


def go_docs_tab():
    st.session_state.add_done = None
    st.session_state.manage_tab = "등록 자료"


def show_done(done):
    ok = done.get("n") is not None
    step_bar(5 if ok else 1)
    with st.container(border=True):
        head = f"✅ {done['title']}" + (f": [{done['short']}] · 청크 {done['n']}개 임베딩" if ok else "")
        (st.success if ok else st.info)(f"**{head}**" + ("  \n챗봇에서 바로 검색됩니다." if ok else ""))
        for line in done.get("lines", []):
            st.markdown(f"- {line}")
        c1, c2 = st.columns(2)
        if c1.button("다른 자료 추가하기", type="primary", use_container_width=True):
            st.session_state.add_done = None
            st.rerun()
        c2.button("등록 자료에서 확인", use_container_width=True, on_click=go_docs_tab)


def ocr_step(path):
    """글자층이 없는 PDF(스캔본·윤곽선 글자)면 OCR을 먼저 하게 한다. 비교·평가로 넘어가도 되면 True."""
    cache = rag.ocr_cache(path)
    if cache:
        st.success(f"🔤 OCR로 읽은 자료입니다 · {ocr.ENGINES[cache['engine']].split('(')[0]} 엔진, {cache['pages_ocr']}쪽"
                   + (f" · 못 읽은 쪽 {cache['pages_failed']}" if cache.get("pages_failed") else "")
                   + ". 답변의 근거는 원본 쪽 이미지로 한 번 더 확인하세요.")
        return True
    low, n = ocr.text_coverage(path)
    if not n or low / n < 0.5:
        return True
    with st.container(border=True):
        st.markdown(f"**🔤 글자를 읽을 수 없는 PDF입니다** · {n}쪽 중 {low}쪽에 글자층이 없습니다(스캔본이거나, 한글 문서를 "
                    "글자 대신 윤곽선으로 인쇄한 PDF). 이대로는 검색·평가를 할 수 없어 먼저 **OCR**(그림에서 글자 읽기)을 합니다.")
        engine = st.radio("OCR 방식", list(ocr.ENGINES), format_func=ocr.ENGINES.get, key="ocr_engine", horizontal=True)
        st.caption("정확(Gemini): 표를 행 단위로 읽고 오탈자가 적습니다. 쪽 이미지가 Google Gemini API로 전송되므로 학생 개인정보가 든 "
                   "문서에는 쓰지 마세요. · 빠름(Windows): 이 PC 안에서만 처리하지만 표·그림 속 글자 오탈자가 많습니다.")
        est = f"약 {max(1, round(low * 22 / 6 / 60))}분" if engine == "gemini" else f"약 {max(1, round(low * 0.6))}초"
        if st.button(f"OCR로 글자 읽기 ({low}쪽, {est})", type="primary"):
            bar = st.progress(0.0, text="OCR 준비 중…")
            try:
                ocr.run(path, engine, progress=lambda d, t: bar.progress(d / max(t, 1), text=f"OCR {d} / {t}쪽"))
            except Exception as e:  # noqa: BLE001
                st.error(f"OCR 중 오류가 났습니다: {e}")
                return False
            st.session_state.pop("dedupe", None)
            st.rerun()
    return False


def tab_add():
    if st.session_state.get("add_done"):
        show_done(st.session_state.add_done)
        return
    up_key = f"add_upload_{st.session_state.get('upload_gen', 0)}"
    up = st.session_state.get(up_key)
    ev = st.session_state.get("eval_report")
    if ev and (up is None or ev[0] != str(INBOX / up.name)):
        ev = None  # 다른 파일을 올렸거나 파일을 뺐으면 이전 평가 결과는 보이지 않게
    step_bar(4 if ev else 3 if up is not None else 1)
    st.markdown("#### 1. PDF 올리기")
    st.caption("올린 PDF는 본문 비교 → AI 평가(12개 기준) → 상담사 승인 순서로 등록됩니다. 승인해야만 등록되고, 등록하면 바로 검색됩니다.")
    up = st.file_uploader("근거 자료로 추가할 PDF", type=["pdf"], key=up_key)
    if up is not None:
        INBOX.mkdir(parents=True, exist_ok=True)
        path = INBOX / up.name
        path.write_bytes(up.getbuffer())
        if ocr_step(str(path)):  # 글자층이 없는 PDF는 OCR을 끝내야 비교·평가로 넘어간다
            same = show_dedupe(str(path), up)
            st.markdown("#### 3. AI 평가")
            show_criteria()
            if st.button("AI 평가 실행 (1분 안팎)", type="primary", disabled=bool(same)):
                with st.spinner("자동 측정 → Gemini 판단 → 최종 판정 중… (1분 안팎)"):
                    try:
                        st.session_state.eval_report = (str(path), agent.evaluate_pdf(path, rag.Index()))
                    except Exception as e:  # noqa: BLE001
                        st.error(f"평가 중 오류가 났습니다: {e}")
                if st.session_state.get("eval_report"):
                    st.rerun()
    if ev:
        path, r = ev
        st.markdown(f"##### 평가 결과 · {r['file']}")
        show_report(r)
        st.divider()
        st.markdown("#### 4. 등록 승인")
        add_form(r, path)


# ---------- 등록 자료 ----------
def tab_docs():
    con = store.connect()
    docs = store.documents(con)
    counts = store.chunk_counts(con)
    alerts = store.open_alerts(con)
    rows = []
    for d, m in docs.items():
        last = store.last_review(con, d)
        rows.append({"인용 이름": m["short"], "자료": m["title"], "구분": "기본" if m["is_base"] else "추가",
                     "상태": "사용 중" if m["status"] == "active" else "사용 중지",
                     "층": LAYER_LABELS[m["layer"]].split("(")[0], "주제": ", ".join(m["topics"]),
                     "청크": counts.get(d, 0), "최근 판정": last["verdict"] if last else "-",
                     "알림": sum(1 for a in alerts if a["doc_id"] == d), "다음 점검": m["next_review_due"] or "-"})
    con.close()
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    target = st.selectbox("자료 선택", list(docs), format_func=lambda d: f"[{docs[d]['short']}] {docs[d]['title']}")
    m = docs[target]
    st.caption(f"파일: data/{m['file_name']} · 등록 {m['added_at']} ({m['added_by']}) · 질문 유형: "
               + ", ".join(TYPE_LABELS.get(q, q) for q in m["qtypes"]))
    c1, c2 = st.columns(2)
    if m["status"] == "active":
        if c1.button("사용 중지", use_container_width=True, help="검색에서만 빼고 자료와 청크는 남깁니다. 다시 켤 수 있습니다."):
            ingest.set_status(target, "inactive")
            flash(f"[{m['short']}] 사용 중지했습니다.")
    elif c1.button("다시 사용", use_container_width=True):
        ingest.set_status(target, "active")
        flash(f"[{m['short']}] 다시 사용합니다.")
    if c2.button("지금 다시 평가", use_container_width=True, disabled=m["status"] != "active"):
        with st.spinner("다시 평가하는 중…"):
            con = store.connect()
            r = periodic_review.review_document(con, rag.Index(), target, rag.DOCS[target])
            con.close()
        flash(f"[{m['short']}] 다시 평가: {r['verdict'] if r else '파일 없음'}. 알림 탭에서 결과를 확인하세요.")
    with st.container(border=True):
        st.markdown("**🗑️ 완전 삭제**")
        confirm = st.checkbox("PDF 파일까지 완전히 지웁니다(되돌릴 수 없음)", key=f"del_{target}")
        typed_ok = True
        if m["is_base"]:
            # 기본 자료는 질문 유형별 확인 순서의 중심이라 한 번 더 확인한다
            st.warning(f"기본 자료입니다. 지우면 [{m['short']}]을 확인하던 질문 유형에서 이 자료가 빠지고, 평가 기록도 함께 지워집니다. "
                       f"자료 추가에서 '비어 있는 기본 자료 자리'로 다시 올리면 원래 역할로 되살아납니다.")
            typed = st.text_input(f"확인을 위해 인용 이름 '{m['short']}'을 입력하세요", key=f"del_typed_{target}")
            typed_ok = typed.strip() == m["short"]
        if st.button("완전 삭제", type="primary", disabled=not (confirm and typed_ok), use_container_width=True):
            removed = ingest.delete_document(target, allow_base=bool(m["is_base"]))
            flash(f"[{m['short']}] 완전 삭제: 저장소의 자료·청크·평가 기록·알림과 파일 {removed}을 지웠습니다."
                  + (" 이 기본 자료 자리는 비어 있습니다." if m["is_base"] else ""))
    hist = store.reviews(store.connect(), target)
    if hist:
        with st.expander(f"평가 기록 {len(hist)}건"):
            st.dataframe(pd.DataFrame(hist), hide_index=True, use_container_width=True)


# ---------- 알림·정기 점검 ----------
def tab_alerts():
    s = periodic_review.status()
    c1, c2, c3 = st.columns(3)
    c1.metric("마지막 정기 점검", s["last"] or "없음")
    c2.metric("다음 점검일", s["next_due"])
    c3.metric("점검 주기", f"{s['interval_days']}일(한 학기)")
    if s["running"]:
        st.info("정기 점검이 실행 중입니다. 끝나면 알림이 여기에 나타납니다(자료 수에 따라 몇 분).")
    elif st.button("지금 정기 점검 실행", help="모든 활성 자료를 다시 평가합니다. 자료당 20초 안팎, Gemini API를 씁니다."):
        with st.spinner("모든 자료를 다시 평가하는 중…"):
            res = periodic_review.run(force=True, log=lambda *_: None)
        flash("정기 점검 완료: " + ", ".join(f"[{rag.DOCS.get(d, {}).get('short', d)}] {v}" for d, v in res))
    st.caption("앱을 열 때 점검 시기가 지났으면 자동으로 점검을 시작합니다. 앱을 열지 않아도 점검하려면 "
               "run_review.bat을 Windows 작업 스케줄러에 등록하세요(README 참고).")
    con = store.connect()
    alerts = store.open_alerts(con)
    con.close()
    st.subheader(f"확인하지 않은 알림 {len(alerts)}건")
    for a in alerts:
        icon, box = SEVERITY[a["severity"]]
        with st.container(border=True):
            st.markdown(f"{icon} **[{a['short']}] {a['title']}** · {a['created_at']}")
            st.write(a["message"])
            if st.button("확인함", key=f"ack_{a['alert_id']}"):
                con = store.connect()
                store.acknowledge_alert(con, a["alert_id"])
                con.commit()
                con.close()
                flash("알림을 확인 처리했습니다.")


def render_alerts():
    """사이드바 알림 아이콘(🔔)을 눌렀을 때 보이는 화면"""
    st.title("🔔 알림·정기 점검")
    if st.session_state.get("manage_msg"):
        st.info(st.session_state.pop("manage_msg"))
    tab_alerts()


def tab_changed():
    # 고른 메뉴를 다시 누르면 선택이 풀리므로 직전 메뉴로 되돌린다
    if st.session_state.manage_tab is None:
        st.session_state.manage_tab = st.session_state.get("manage_tab_last", "자료 추가")
    st.session_state.manage_tab_last = st.session_state.manage_tab


def render():
    st.title("자료 관리")
    if st.session_state.get("manage_msg"):
        st.info(st.session_state.pop("manage_msg"))
    # 고른 메뉴는 더 진한 연두(ui_style). 다시 그려도 선택이 유지된다
    tabs = {"자료 추가": tab_add, "등록 자료": tab_docs}
    if st.session_state.get("manage_tab") not in tabs:
        st.session_state.manage_tab = st.session_state.get("manage_tab_last", "자료 추가")
    st.segmented_control("메뉴", list(tabs), key="manage_tab", label_visibility="collapsed", on_change=tab_changed)
    st.divider()
    tabs[st.session_state.manage_tab]()
    reports = sorted(Path(agent.REPORT_DIR).glob("*.json"), key=lambda x: -x.stat().st_mtime) if Path(agent.REPORT_DIR).exists() else []
    if reports:
        with st.expander(f"자료 추가 때의 평가 기록 {len(reports)}건 (doc_agent/reports/)"):
            rows = []
            for f in reports:
                r = json.loads(f.read_text(encoding="utf-8"))
                rows.append({"파일": r["file"], "판정": r["verdict"], "점수": r["weighted_score"],
                             "평가일": r["evaluated_at"], "사유": " / ".join(r["reasons"])})
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
