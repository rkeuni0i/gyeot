"""학교폭력·생활지도 사안처리 도우미 (이산중학교 1인 상담사용 테스트 챗봇).

실행:  run.bat 더블클릭
   또는 C:\\Users\\2003g\\anaconda3\\envs\\DS\\Scripts\\streamlit.exe run app.py
   → 브라우저 http://localhost:8501

근거 자료: 2026학년도 학교폭력 사안처리 가이드북 + 이산중학교 학생생활규정(2026.8.27 개정)
⚠️ 실제 학생 이름·학교명 등 개인정보는 입력하지 않는다. 질문과 검색된 원문 일부가 Google Gemini API로 전송된다.
"""
import base64
import html
import re
from pathlib import Path

import pymupdf
import streamlit as st

import add_page
import conversations
import store
import ui_style
from doc_agent import periodic_review
import streamlit.components.v1 as components

from rag import (DB_PATH, DOCS, LLM_MODEL, LOCAL_AGENCIES, QTYPES, ROOT, SCHOOL_INFO, SCHOOL_NAME, Index,
                 cite_label, finish, log_turn, plan_for, prepare, stream_answer)

EXAMPLES = [
    "수업 시간에 학생 상담을 해도 되나요? 상담 내용 비밀은 어디까지 지켜야 하나요?",
    "학교폭력은 아닌데 수업을 계속 방해하는 학생은 어떻게 처리하나요?",
    "출석정지를 받은 학생을 Wee센터 특별교육에 보낼 수 있나요? 출결은 어떻게 되나요?",
    "학생이 단톡방에서 저격글과 욕설을 계속 받는다고 상담실에 왔어요. 신고는 무섭대요. 절차상 뭘 해야 하나요?",
    "학교폭력 신고를 받은 뒤 교육지원청 보고는 언제까지 해야 하나요?",
    "가해학생과 피해학생 분리는 며칠까지 할 수 있고, 피해학생이 원하지 않으면요?",
    "학교장 자체해결이 가능한 요건이 뭔가요?",
    "성추행 피해를 상담 중에 알게 됐는데 학생이 비밀로 해달래요.",
    "사이버 비행을 학생에게 이해하기 쉽게 설명하려면 뭐라고 하면 돼?",
    "사이버 비행이 뭐야?",
    "사이버폭력 가해 학생을 개인상담할 때 회기를 어떻게 구성하면 되나요?",
    "사이버폭력 피해 학생을 상담할 때 효과적인 개입 방법은 무엇인가요?",
    "단톡방에서 친구를 저격한 가해 학생과 상담하게 됐어요. 학교 절차와 상담에서 다룰 내용을 알려주세요.",
]

# 첫 화면에 보여 줄 예시(유형별로 하나씩)
EXAMPLES_SHORT = [EXAMPLES[i] for i in (0, 4, 6, 7, 9, 10)]

BRAND = ROOT / "docs" / "brand"  # 로고: docs/brand/make_logo.py로 만든다
st.set_page_config(page_title="곁 · 이산중 상담 도우미", page_icon=str(BRAND / "gyeot_icon_256.png"), layout="wide")


@st.cache_resource(show_spinner="검색 인덱스 불러오는 중…")
def load_index():
    if not DB_PATH.exists():
        st.error(f"자료 저장소(index/{DB_PATH.name})가 없습니다. 파일 이름을 바꿨거나 옮겼다면 되돌리고, "
                 "처음 설치라면 build_index.py를 실행하세요. (빈 저장소는 자동으로 만들지 않습니다)")
        st.stop()
    return Index()


def doc_alerts(doc_ids, severities=("critical", "warn")):
    """인용된 자료의 확인하지 않은 알림(사용 전 확인)."""
    con = store.connect()
    out = [a for d in doc_ids for a in store.open_alerts(con, d) if a["severity"] in severities]
    con.close()
    return out


def show_use_alerts(res):
    """답변이 인용한 자료에 주의·경고 알림이 있으면 답변 바로 아래에 보여준다."""
    cited_docs = list(dict.fromkeys(d for d, _ in res["cited"]))
    alerts = doc_alerts(cited_docs)
    if alerts:
        lines = "\n".join(f"- {'🔴' if a['severity'] == 'critical' else '🟠'} [{a['short']}] {a['title']}: "
                          f"{(a['message'] or '')[:160]}" for a in alerts[:5])
        st.warning(f"**사용 전 확인** · 이 답변이 인용한 자료에 확인하지 않은 알림이 {len(alerts)}건 있습니다.\n{lines}\n\n"
                   "화면 오른쪽 위 🔔 아이콘(알림·정기 점검)에서 확인할 수 있습니다.")


@st.cache_data(show_spinner=False)
def page_png(doc, page, dpi=110):
    return pymupdf.open(ROOT / "data" / DOCS[doc]["file"])[page - 1].get_pixmap(dpi=dpi).tobytes("png")


def join_chunks(texts):
    """같은 쪽 청크를 순서대로 잇는다. 청크 사이 겹치는 부분(최대 200자)은 한 번만 남긴다."""
    out = texts[0]
    for t in texts[1:]:
        k = next((n for n in range(min(200, len(out), len(t)), 0, -1) if out.endswith(t[:n])), 0)
        out += ("" if k else "\n") + t[k:]
    return out


def group_by_page(sources):
    """검색 결과를 (문서, 쪽) 단위로 묶는다. 순서는 그 쪽에서 가장 앞선 청크 순위, 점수는 가장 높은 값."""
    pages = {}
    for h in sources:  # sources는 문서별 하이브리드 순위 순(가이드북 → 학교 규정)
        g = pages.setdefault((h["doc"], h["page"]),
                             {"doc": h["doc"], "page": h["page"], "section": h["section"], "chunks": []})
        g["chunks"].append(h)
    groups = []
    for g in pages.values():
        ch = g["chunks"]
        best = max(ch, key=lambda h: h["cos"])
        groups.append({**g, "cos": best["cos"], "bm25": max(h["bm25"] for h in ch),
                       "vec_rank": min(h["vec_rank"] for h in ch), "bm25_rank": min(h["bm25_rank"] for h in ch),
                       "matched_query": best.get("matched_query", ""), "n_chunks": len(ch),
                       "text": join_chunks([h["text"] for h in sorted(ch, key=lambda h: h["id"])])})
    return groups


def show_route(res):
    """질문 유형과 그에 따른 문서 확인 순서를 답변 위에 보여준다."""
    if not res.get("qtype"):
        return
    order = " → ".join(f"{i + 1}. {DOCS[d]['short']}" for i, d in enumerate(res["doc_order"]) if d in DOCS)
    types = " + ".join(QTYPES[t]["label"] for t in res.get("qtypes") or [res["qtype"]])
    st.caption(f"🔎 질문 유형: **{types}** · 확인 순서: {order}")
    if res.get("parts"):  # 질문 분해: 조각마다 유형과 질문 속 표현
        marks = "①②③④⑤"
        st.caption("🧩 질문 분해: " + " · ".join(f"{marks[i]} **{pt['aspect']}**({QTYPES[pt['type']]['label']})"
                                              for i, pt in enumerate(res["parts"])))


def show_weak_cites(res):
    """답변 문장과 인용한 쪽의 핵심어 겹침이 낮은 줄(근거 약함)을 알려 준다."""
    weak = res.get("weak_cites") or []
    if not weak:
        return
    with st.expander(f"⚠️ 근거 약함 {len(weak)}곳 · 문장과 인용한 쪽의 내용이 거의 겹치지 않습니다(원문 확인 필요)"):
        for w in weak:
            sem = f" · 뜻 유사도 {w['sem']:.2f}" if w.get("sem") is not None else ""
            st.markdown(f"- {html.escape(w['line'])} → {', '.join(w['cites'])} · 단어 겹침 {w['overlap']:.0%}{sem}")
        st.caption("단어 겹침(문장 핵심어 중 인용 쪽에 나오는 비율)이 35% 미만이고, 뜻 유사도(문장과 인용 쪽의 임베딩 유사도)도 "
                   "0.70 미만인 줄입니다. 근거 원문 보기에서 해당 쪽을 확인하세요.")


def show_sources(res):
    import pandas as pd
    cited = set(map(tuple, res["cited"]))
    if res["uncited_ok"]:
        st.warning("검색 자료에 없는 쪽을 인용했습니다. 원문을 확인하세요: "
                   + ", ".join(cite_label(d, p) for d, p in res["uncited_ok"]))
    st.markdown("**📄 근거** · 인용된 쪽: " + (", ".join(cite_label(d, p) for d, p in res["cited"]) or "없음"))
    groups = sorted(group_by_page(res["sources"]), key=lambda g: -g["cos"])  # 문서 구분 없이 코사인 높은 순
    rows = [{"순위": i + 1, "문서": DOCS[g["doc"]]["short"], "PDF 쪽": g["page"],
             "인용": "✅" if (g["doc"], g["page"]) in cited else "",
             "벡터 유사도(코사인)": round(g["cos"], 3), "BM25": round(g["bm25"], 1),
             "청크 수": g["n_chunks"], "장": g["section"]}
            for i, g in enumerate(groups)]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                 column_config={"벡터 유사도(코사인)": st.column_config.ProgressColumn(
                     "벡터 유사도(코사인)", min_value=0.0, max_value=1.0, format="%.3f")})
    st.caption("순위 = 벡터 유사도(코사인) 높은 순, 문서 구분 없음. 검색은 질문 유형의 확인 순서대로 문서마다 따로 찾아 합치며, "
               "1순위 문서에 가장 많은 청크를 배정합니다(가이드북은 본문이 가리키는 부록 양식 쪽도 포함). 벡터 유사도는 질문(또는 생성 검색어)과 청크의 Gemini "
               "임베딩 코사인 유사도이며, 같은 청크가 여러 검색어에 걸리면 가장 높은 값을 표시합니다. 한 쪽이 길어 청크가 "
               "나뉜 경우 한 줄로 합치고, 점수는 그 쪽 청크 중 가장 높은 값을 표시합니다.")
    shown = [g for g in groups if (g["doc"], g["page"]) in cited]  # 답변에 인용된 쪽만
    with st.expander(f"근거 원문 보기 (답변에 인용된 {len(shown)}개 쪽, 유사도 높은 순)"):
        if res.get("standalone"):
            st.caption("검색에 쓴 질문: " + res["standalone"])
        if res.get("queries"):
            st.caption("생성된 검색어: " + " · ".join(res["queries"]))
        hidden = len(groups) - len(shown)
        if hidden:
            st.caption(f"검색됐지만 답변에 인용되지 않은 {hidden}개 쪽은 원문 목록에서 제외했습니다(위 표에는 표시).")
        if not shown:
            st.info("답변에 인용된 근거가 없습니다.")
        for g in sorted(shown, key=lambda x: -x["cos"]):  # 벡터 유사도 높은 순
            parts = f" · 청크 {g['n_chunks']}개 합침" if g["n_chunks"] > 1 else ""
            label = cite_label(g["doc"], g["page"])
            st.markdown(f"**{label}** · {g['section']} · ✅ 인용됨 · 코사인 {g['cos']:.3f}"
                        f" · BM25 {g['bm25']:.1f}{parts} · 매칭 검색어: {g['matched_query']}")
            st.text(g["text"])
            if st.toggle(f"{label} 원본 쪽 이미지", key=f"img-{id(res)}-{g['doc']}-{g['page']}"):
                st.image(page_png(g["doc"], g["page"]))
            st.divider()


# 답변 본문 보기(화면 전체 글자 30px 기준, 본문은 조금만 더 크게), 첫 줄 요약은 강조 상자, 소제목은 작게, 근거 표시는 작은 회색
ANSWER_CSS = """<style>
[class*="st-key-ans_"] [data-testid="stMarkdownContainer"] { font-size: 1.05rem; line-height: 1.75; }
[class*="st-key-ans_"] [data-testid="stMarkdownContainer"] p,
[class*="st-key-ans_"] [data-testid="stMarkdownContainer"] li { font-size: inherit; }
[class*="st-key-ans_"] [data-testid="stMarkdownContainer"] li { margin-bottom: .35em; }
[class*="st-key-ans_"] [data-testid="stMarkdownContainer"] > p:first-child {
    background: #FFFFFF; border-left: 5px solid #7DB84A; border-radius: 10px;
    padding: .6em .9em; }
[class*="st-key-ans_"] [data-testid="stMarkdownContainer"] h2 {
    font-size: 1.3rem; margin: 1.1em 0 .4em; padding-bottom: .25em;
    border-bottom: 1px solid rgba(128,128,128,.3); }
[class*="st-key-ans_"] [data-testid="stMarkdownContainer"] h3 { font-size: 1.15rem; margin: .9em 0 .3em; }
[class*="st-key-ans_"] [data-testid="stMarkdownContainer"] strong { font-weight: 700; }
[class*="st-key-ans_"] .mg-cite { font-size: .72em; color: #8a8f98; white-space: nowrap; font-weight: 400; }
</style>"""
CITE_RE = re.compile(r"\[[^\[\]]*?(?:p\.\s?\d[^\[\]]*|지역기관|일반 원칙)\]")


def render_answer(text, key):
    """답변 마크다운을 읽기 쉽게 보여 준다. <양식1-1> 같은 꺾쇠가 HTML로 사라지지 않게 먼저 이스케이프한다."""
    safe = html.escape(text, quote=False)
    safe = CITE_RE.sub(lambda m: f'<span class="mg-cite">{m.group(0)}</span>', safe)
    with st.container(key=f"ans_{key}"):
        st.markdown(safe, unsafe_allow_html=True)


index = load_index()
ui_style.apply()
st.markdown(ANSWER_CSS, unsafe_allow_html=True)


@st.cache_data
def img_b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode()


def bell_badge_css(count, running, active=False):
    """알림 버튼 오른쪽 위에 빨간 숫자 배지(휴대폰 앱 아이콘처럼). 0건이면 숨긴다. 버튼 모양은 ui_style."""
    label = "99+" if count > 99 else str(count)
    badge = "" if not count else f"""
    .st-key-bell_btn button::after {{
        content: "{label}"; position: absolute; top: -6px; right: -6px;
        min-width: 32px; height: 32px; padding: 0 8px; box-sizing: border-box;
        border-radius: 16px; background: #e5484d; color: #fff;
        font-size: 17px; font-weight: 700; line-height: 32px; text-align: center;
        box-shadow: 0 0 0 2px #fff;
    }}"""
    spin = "" if not running else """
    .st-key-bell_btn button::before {
        content: ""; position: absolute; bottom: -2px; right: -2px; width: 16px; height: 16px;
        border-radius: 50%; background: #3b82f6; box-shadow: 0 0 0 2px #fff;
    }"""
    on = ".st-key-bell_btn button { background: #A9CF7A !important; }" if active else ""
    st.markdown(f"<style>{badge}{spin}{on}</style>", unsafe_allow_html=True)


def go_alerts():
    st.session_state.view = "alerts"


def go_manage():
    st.session_state.view = "manage"


def leave_view():
    """자료·알림 화면에서 지금 대화로 돌아간다."""
    st.session_state.view = None


leave_alerts = leave_view  # 이전 이름(알림 화면의 돌아가기)


def new_chat():
    st.session_state.messages = []
    st.session_state.conv_id = None
    st.session_state.view = None


def open_conv(conv_id):
    st.session_state.messages = conversations.load(conv_id)
    st.session_state.conv_id = conv_id
    st.session_state.view = None


def delete_conv():
    if st.session_state.get("conv_id"):
        conversations.delete(st.session_state.conv_id)
    new_chat()


def pick_example():
    if st.session_state.get("ex_pick"):
        st.session_state.pending = st.session_state.ex_pick
    st.session_state.ex_pick = None


st.session_state.setdefault("messages", [])
st.session_state.setdefault("view", None)

with st.sidebar:
    # 학기별 정기 점검: 앱을 열었을 때 점검 시기가 지났으면 백그라운드로 한 번 시작
    if "review_checked" not in st.session_state:
        st.session_state.review_checked = True
        if periodic_review.start_background_if_due():
            st.session_state.review_started = True
    rs = periodic_review.status()
    _con = store.connect()
    _alerts = store.open_alerts(_con)
    _con.close()
    n_crit = sum(a["severity"] == "critical" for a in _alerts)
    n_warn = sum(a["severity"] == "warn" for a in _alerts)

    st.image(str(BRAND / "gyeot_logo.png"), width=380)
    st.markdown(f"**학교** : {SCHOOL_NAME}  \n{SCHOOL_INFO}  \n**연계 기관**  \n"
                + "  \n".join(f"· {a['name']} ☎ {a['phone']}" for a in LOCAL_AGENCIES))

    view = st.session_state.get("view")
    c_new, c_files = st.columns(2, vertical_alignment="center")
    c_new.button("새 채팅", icon=":material/add:", key="new_chat", on_click=new_chat, use_container_width=True)
    c_files.button("자료보관", icon=":material/folder_open:", key="files_btn_on" if view == "manage" else "files_btn",
                   on_click=go_manage, help="자료 관리: 자료 추가·등록 자료", use_container_width=True)
    page = {"manage": "자료 관리", "alerts": "알림"}.get(view, "챗봇")
    st.divider()

    order = [d for d in ("esan", "guide") if d in DOCS] + [d for d in DOCS if d not in ("esan", "guide")]
    with st.expander(f"근거 자료 {len(order)}개"):
        st.markdown("\n".join(f"{i + 1}. [{DOCS[d]['short']}] {DOCS[d]['name']}" for i, d in enumerate(order)))
        st.caption("자료 관리에서 추가·교체하면 여기에 바로 반영됩니다.")
    st.divider()

    st.markdown('<div class="gy-side-title">이전 대화</div>', unsafe_allow_html=True)
    convs = conversations.list_all()
    if not convs:
        st.caption("아직 저장된 대화가 없습니다.")
    for c in convs:
        on = c["id"] == st.session_state.get("conv_id")
        st.button(c["title"], key=("conv_on_" if on else "conv_") + c["id"], icon=":material/chat_bubble:",
                  type="tertiary", on_click=open_conv, args=(c["id"],), use_container_width=True,
                  help=f"{c['updated_at'].replace('T', ' ')} · 질문 {c['n']}개")
    st.divider()
    with st.popover("설정", icon=":material/settings:", use_container_width=True):
        st.markdown(f"**모델** `{LLM_MODEL}`")
        st.caption("실제 학생 이름, 학교명, 연락처는 입력하지 마세요. 질문 내용이 Google Gemini API로 전송됩니다.")
        st.caption("대화는 logs/conversations/, 질문·답변 기록은 logs/chat_log.jsonl에 이 컴퓨터에만 저장됩니다.")
        if st.session_state.get("conv_id"):
            st.button("지금 대화 삭제", icon=":material/delete:", on_click=delete_conv, use_container_width=True)

# 알림 아이콘: 화면 오른쪽 위(Streamlit 실행 표시 자리)에 고정. 위치·모양은 ui_style
bell_badge_css(len(_alerts), rs["running"], active=(page == "알림"))
st.button(":material/notifications:", key="bell_btn", on_click=go_alerts,
          help=(f"알림·정기 점검 · 확인하지 않은 알림 {len(_alerts)}건 (긴급 {n_crit}, 주의 {n_warn})"
                f" · 다음 정기 점검 {rs['next_due']}" + (" · 지금 점검 중" if rs["running"] else "")))

if page == "알림":
    st.button("채팅으로 돌아가기", icon=":material/arrow_back:", on_click=leave_view)
    add_page.render_alerts()
    st.stop()

if page == "자료 관리":
    st.button("채팅으로 돌아가기", icon=":material/arrow_back:", on_click=leave_view, key="back_manage")
    add_page.render()
    st.stop()

USER_AVATAR, AI_AVATAR = ":material/person:", str(BRAND / "gyeot_mark_128.png")

if not st.session_state.messages and not st.session_state.get("pending"):
    st.markdown(f'<div class="gy-hello"><img src="data:image/png;base64,{img_b64(BRAND / "gyeot_mark_128.png")}" alt="">'
                '<h2>무엇을 도와드릴까요?</h2>'
                '<p>질문 유형에 맞는 자료부터 확인해 답하고, 문장마다 [이산중 p.쪽]·[가이드북 p.쪽]처럼 근거를 답니다.</p></div>',
                unsafe_allow_html=True)
    st.pills("예시 질문", EXAMPLES_SHORT, key="ex_pick", on_change=pick_example, label_visibility="collapsed")
    st.caption("⚠️ 실제 학생 이름, 학교명, 연락처는 입력하지 마세요. 질문 내용이 Google Gemini API로 전송됩니다. "
               "답변이 맞는지 근거 원문으로 꼭 확인하세요.")

def request_stop():
    st.session_state.stop_requested = True


def save_conversation():
    if not st.session_state.get("conv_id"):
        st.session_state.conv_id = conversations.new_id()
    conversations.save(st.session_state.conv_id, st.session_state.messages)


# 답변 생성 중에 멈췄으면(정지 버튼 또는 다른 버튼을 눌러 다시 그려짐) 그때까지 쓴 답을 대화에 남긴다
_partial = st.session_state.pop("partial", None)
if _partial:
    done = _partial["text"].strip()
    st.session_state.messages.append({"role": "user", "content": _partial["question"]})
    st.session_state.messages.append({"role": "assistant", "stopped": True,
                                      "content": (done + "\n\n" if done else "") + "_⏹ 답변 생성을 멈췄습니다._"})
    st.session_state.pop("stop_requested", None)
    save_conversation()

STOP_JS = """<script>
// 정지 버튼을 입력창의 보내기 버튼 자리에 겹쳐 놓는다(답변 생성 중에만 그려짐)
const doc = window.parent.document;
function place() {
  const sub = doc.querySelector('[data-testid="stChatInputSubmitButton"]');
  const wrap = doc.querySelector('.st-key-stop_gen');
  if (!sub || !wrap) return;
  const r = sub.getBoundingClientRect();
  Object.assign(wrap.style, {position: 'fixed', left: r.left + 'px', top: r.top + 'px',
                             width: r.width + 'px', height: r.height + 'px', zIndex: 1000000, visibility: 'visible'});
}
place(); setInterval(place, 150);
</script>"""


def show_stop_button():
    """답변 생성 중: 보내기 아이콘을 숨기고 그 자리에 정지 아이콘을 띄운다. 누르면 생성을 멈춘다."""
    st.markdown("<style>[data-testid='stChatInputSubmitButton']{visibility:hidden !important}</style>",
                unsafe_allow_html=True)
    st.button(":material/stop:", key="stop_gen", on_click=request_stop, help="답변 생성 멈추기")
    components.html(STOP_JS, height=0)


for i, m in enumerate(st.session_state.messages):
    with st.chat_message(m["role"], avatar=USER_AVATAR if m["role"] == "user" else AI_AVATAR):
        if m.get("result"):
            st.markdown('<div class="gy-ai-label">곁</div>', unsafe_allow_html=True)
            show_route(m["result"])
            render_answer(m["content"], i)
            show_use_alerts(m["result"])
            show_weak_cites(m["result"])
            show_sources(m["result"])
        else:
            st.markdown(m["content"])

question = st.chat_input("상황이나 궁금한 절차를 입력하세요")
if st.session_state.get("pending"):
    question = st.session_state.pop("pending")

if question:
    with st.chat_message("user", avatar=USER_AVATAR):
        st.markdown(question)
    history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]
    # 멈추면 다음 실행에서 이 기록으로 대화에 남긴다(끝까지 쓰면 지운다)
    st.session_state.partial = {"question": question, "text": ""}
    show_stop_button()
    with st.chat_message("assistant", avatar=AI_AVATAR):
        st.markdown('<div class="gy-ai-label">곁</div>', unsafe_allow_html=True)
        status = st.empty()
        status.caption("🔎 질문 유형을 판단하고 맞는 자료부터 찾는 중…")
        try:
            ctx = prepare(index, question, history=history)
        except Exception as e:
            st.session_state.pop("partial", None)
            status.empty()
            st.error(f"답변 생성 중 오류가 났습니다: {e}")
            st.stop()
        status.empty()
        if "crisis" in ctx["flags"]:
            st.error("자해·자살 위험 신호가 감지되었습니다. "
                     "학교 위기관리 절차를 우선 따르세요.")
        show_route(ctx)
        box = st.empty()
        box.caption("✍️ 답변을 쓰는 중… (정지 버튼으로 멈출 수 있습니다)")
        text = ""
        try:
            for piece in stream_answer(ctx):
                text += piece
                st.session_state.partial["text"] = text
                box.markdown(text + " ▌")  # 조각마다 화면에 그림: 정지를 누르면 여기서 멈춘다
        except Exception as e:
            st.session_state.pop("partial", None)
            st.error(f"답변 생성 중 오류가 났습니다: {e}")
            st.stop()
        res = finish(ctx, text, index)
        box.empty()
        render_answer(res["answer"], len(st.session_state.messages) + 1)
        show_use_alerts(res)
        show_weak_cites(res)
        show_sources(res)
    st.session_state.pop("partial", None)
    log_turn(question, res)
    st.session_state.messages.append({"role": "user", "content": question})
    st.session_state.messages.append({"role": "assistant", "content": res["answer"], "result": res})
    # 이전 대화로 저장(첫 질문이면 새 대화 ID) → 사이드바 목록에 바로 보이도록 다시 그린다
    save_conversation()
    st.rerun()
