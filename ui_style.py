"""화면 공통 스타일(Figma 시안: 연한 연두 바탕, 흰 사이드바, 연두 알약 버튼, 고른 항목은 더 진한 연두)."""
import streamlit as st

PILL = "#DDEBC4"        # 알약 기본
PILL_ACTIVE = "#A9CF7A"  # 고른 메뉴·지금 단계(더 진하게)
PILL_DONE = "#E7F1D3"    # 지난 단계
INK = "#1C2A18"
MUTED = "#6B7A63"
GREEN = "#4E8A2E"     # 진한 연두(아이콘·테두리)

# 주제별 색(등록 자료 표 등). 서로 구분되는 부드러운 색 7개, 글자는 진한 색 그대로
TOPIC_COLORS = {
    "학교폭력": "#E9A3A3",        # 장밋빛
    "가정": "#F1C48E",            # 살구
    "학업·학교부적응": "#E6D47F",  # 모래빛 노랑
    "정서·위기": "#C9A8E2",        # 라벤더
    "디지털·사이버": "#93C6EA",    # 하늘
    "상담 기법": "#9ED2B2",        # 민트
    "학교 운영": "#C8C3B4",        # 따뜻한 회색
}


def topic_column(label="주제"):
    """st.dataframe에서 주제 목록을 주제별 색 라벨로 보여 주는 열 설정."""
    return st.column_config.MultiselectColumn(label, options=list(TOPIC_COLORS), color=list(TOPIC_COLORS.values()))


PRETENDARD = "https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/variable/woff2/PretendardVariable.woff2"

GLOBAL_CSS = f"""<style>
/* 글꼴: Pretendard 가변 글꼴(SIL OFL). 테마(config.toml)의 font가 이 이름을 쓴다. 못 불러오면 맑은 고딕 */
@font-face {{ font-family: "Pretendard"; font-weight: 45 920; font-style: normal; font-display: swap;
    src: url("{PRETENDARD}") format("woff2-variations"), url("{PRETENDARD}") format("woff2"); }}
html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"], button, input, textarea {{
    font-family: "Pretendard", "Malgun Gothic", sans-serif; }}
/* 본문 폭과 여백 */
/* 화면 크기에 맞춰 시작·끝 여백을 비율로: 좁으면 1.25rem, 넓으면 화면의 4%(최대 5rem). 아주 넓은 화면에서도 2000px까지만 */
[data-testid="stMainBlockContainer"], [data-testid="stBottomBlockContainer"], .block-container {{
    max-width: 2000px !important; width: 100%;
    padding-left: clamp(1.25rem, 4vw, 5rem) !important; padding-right: clamp(1.25rem, 4vw, 5rem) !important; }}
[data-testid="stMainBlockContainer"] {{ padding-top: clamp(1.5rem, 4vh, 3.5rem) !important;
    padding-bottom: clamp(2rem, 6vh, 5rem) !important; }}

/* 사이드바: 흰 카드 느낌, 글자는 조금 작게 */
/* 글자가 커진 만큼 사이드바 기본 폭도 넓게(오른쪽 끝을 끌어 조절 가능) */
/* 사이드바가 본문 위에 떠 있는 것처럼: 경계선 대신 오른쪽으로 옅은 그림자 */
[data-testid="stSidebar"] {{ border-right: none; min-width: 480px; z-index: 999991;  /* 위쪽 머리줄(z 999990)보다 위: 그림자가 끝까지 */
    box-shadow: 6px 0 24px rgba(40, 48, 32, .10), 1px 0 0 rgba(40, 48, 32, .06); }}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] li {{ font-size: .8rem; line-height: 1.55; }}
[data-testid="stSidebar"] [data-testid="stExpander"] summary p {{ font-size: .82rem; font-weight: 600; }}
[data-testid="stSidebar"] hr {{ margin: .6rem 0; border-color: #E6E8E2; }}
/* 사이드바 접기 버튼은 항상 보이게, 오른쪽 끝을 끌어 폭 조절(손잡이에 연두색) */
[data-testid="stSidebarCollapseButton"] {{ visibility: visible !important; opacity: 1 !important; }}
[data-testid="stSidebarResizeHandle"]:hover {{ background: {PILL_ACTIVE}; }}
.gy-side-title {{ font-size: .85rem; font-weight: 700; color: {INK}; margin: .2rem 0 .1rem; }}

/* 알약 메뉴(챗봇/자료 관리, 자료 추가/등록 자료): 고른 것은 더 진한 연두 */
[data-testid="stButtonGroup"] {{ gap: .5rem; }}
[data-testid="stButtonGroup"] button {{
    border: none !important; border-radius: 999px !important; background: {PILL} !important;
    color: {INK} !important; padding: .35rem 1.3rem !important; margin-right: .45rem; box-shadow: none !important; }}
[data-testid="stButtonGroup"] button[aria-checked="true"] {{ background: {PILL_ACTIVE} !important; font-weight: 700; }}
[data-testid="stButtonGroup"] button[aria-checked="false"]:hover {{ background: #CFE2AE !important; }}
[data-testid="stButtonGroup"] button p {{ font-weight: inherit; }}
.st-key-ex_pick [data-testid="stButtonGroup"] {{ justify-content: center; }}
.st-key-ex_pick [data-testid="stButtonGroup"] button {{ background: #FFFFFF !important; font-weight: 400;
    box-shadow: 0 2px 8px rgba(60, 90, 30, .08) !important; }}
.st-key-ex_pick [data-testid="stButtonGroup"] button:hover {{ background: {PILL_DONE} !important; }}
.st-key-ex_pick [data-testid="stButtonGroup"] button, .st-key-ex_pick [data-testid="stButtonGroup"] button p {{
    white-space: normal !important; overflow: visible !important; text-overflow: clip !important; max-width: 100%; height: auto; }}
/* 사이드바 챗봇·자료 관리 버튼 사이 20pt(약 27px) */
[data-testid="stSidebar"] [data-testid="stButtonGroup"] > div {{ column-gap: 27px !important; }}
[data-testid="stSidebar"] [data-testid="stButtonGroup"] button {{ flex: 1 1 0; margin-right: 0 !important; }}

/* 기본 버튼도 연두 알약 */
[data-testid="stBaseButton-secondary"] {{ background: {PILL}; border: none; color: {INK}; }}
[data-testid="stBaseButton-secondary"]:hover {{ background: #CFE2AE; color: {INK}; }}

/* 새 대화 버튼 */
.st-key-new_chat button {{ background: {PILL} !important; border: none !important; color: {INK} !important;
    font-weight: 600; min-height: 66px; }}
.st-key-new_chat button:hover {{ background: #CFE2AE !important; }}

/* 이전 대화 목록: 아이콘 + 제목 한 줄, 지금 대화는 연두 배경 */
[class*="st-key-conv_"] button {{ justify-content: flex-start !important; text-align: left; width: 100%;
    padding: .3rem .4rem !important; border-radius: 10px !important; color: {INK} !important; }}
[class*="st-key-conv_"] button p {{ font-size: .8rem !important; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }}
[class*="st-key-conv_"] button:hover {{ background: #F1F7E6 !important; }}
[class*="st-key-conv_on_"] button {{ background: {PILL_DONE} !important; font-weight: 700; }}

/* 대화 영역: 말풍선 배경 없이, 질문과 답 사이 가는 선 */
[data-testid="stChatMessage"] {{ background: transparent !important; padding: .9rem 0; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {{
    border-top: 1px solid #DFE2DA; margin-top: .6rem; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stMarkdownContainer"] p {{
    font-weight: 600; }}
.gy-ai-label {{ font-size: .72rem; font-weight: 700; letter-spacing: .04em; color: {PILL_ACTIVE};
    color: #5E9C3A; margin-bottom: .2rem; }}

/* 입력창: 흰 알약 + 초록 원형 보내기 버튼 */
[data-testid="stBottom"] > div {{ background: transparent; }}
[data-testid="stChatInput"] {{ background: #FFFFFF !important; border: none !important; border-radius: 999px !important;
    box-shadow: 0 6px 22px rgba(60, 90, 30, .14); padding: .2rem .4rem .2rem 1rem; }}
[data-testid="stChatInput"] > div, [data-testid="stChatInput"] textarea {{ background: #FFFFFF !important; }}
[data-testid="stChatInputSubmitButton"] {{ background: {PILL_ACTIVE} !important; border-radius: 999px !important;
    color: #FFFFFF !important; width: 60px; height: 60px; }}

/* 빈 대화 첫 화면 */
.gy-hello {{ text-align: center; margin: 7vh 0 1.2rem; }}
.gy-hello img {{ width: 120px; }}
.gy-hello h2 {{ margin: .4rem 0 .2rem; font-size: 1.6rem; }}
.gy-hello p {{ color: {MUTED}; font-size: .9rem; margin: 0; }}

/* 사이드바 원형 아이콘: 자료(연두 바탕) · 알림(흰 바탕 + 진한 연두 테두리), 아이콘은 진한 연두 */
[class*="st-key-files_btn"] button, .st-key-bell_btn button {{
    position: relative; overflow: visible; width: 64px; height: 64px; min-height: 64px; padding: 0;
    border-radius: 50% !important; box-shadow: none !important; }}
[class*="st-key-files_btn"] button {{ background: {PILL} !important; border: none !important; }}
[class*="st-key-files_btn"] button:hover {{ background: #CFE2AE !important; }}
.st-key-files_btn_on button {{ background: {PILL_ACTIVE} !important; }}
.st-key-bell_btn button {{ background: #FFFFFF !important; border: 2.5px solid {GREEN} !important; }}
.st-key-bell_btn button:hover {{ background: {PILL_DONE} !important; }}
[class*="st-key-files_btn"] button span, [class*="st-key-files_btn"] button p,
.st-key-bell_btn button span, .st-key-bell_btn button p {{ color: {GREEN} !important; font-size: 1.45rem; }}
.st-key-new_chat button {{ min-height: 64px !important; }}

/* 사이드바 모서리 25px 통일(원형 아이콘 제외) · 좌우 여백 38px 통일 */
[data-testid="stSidebar"] {{ --gy-side-pad: 38px; }}
[data-testid="stSidebarUserContent"] {{ padding-left: var(--gy-side-pad) !important; padding-right: var(--gy-side-pad) !important; }}
[data-testid="stSidebar"] .st-key-new_chat button,
[data-testid="stSidebar"] [data-testid="stExpander"] details,
[data-testid="stSidebar"] [data-testid="stPopover"] button,
[data-testid="stSidebar"] [class*="st-key-conv_"] button {{ border-radius: 25px !important; }}
[data-testid="stSidebar"] [class*="st-key-conv_"] button {{ padding: .35rem .8rem !important; }}
/* 새 채팅·자료·알림 줄: 새 채팅은 남는 폭, 아이콘은 64px 고정 → 오른쪽 끝도 여백에 딱 맞게 */
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"]:has(.st-key-new_chat) {{ gap: 12px !important; }}
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"]:has(.st-key-new_chat) > div:first-child {{ flex: 1 1 auto !important; width: auto !important; min-width: 0 !important; }}
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"]:has(.st-key-new_chat) > div:not(:first-child) {{
    flex: 0 0 64px !important; width: 64px !important; min-width: 64px !important; }}
</style>"""


def apply():
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)
