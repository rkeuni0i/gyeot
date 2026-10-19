"""'곁' 로고 만들기: 서로 기댄 잎 두 장(마크) + '곁' 글자(워드마크).

실행: python docs/brand/make_logo.py  → 같은 폴더에 SVG·PNG를 만든다.
글자는 Noto Sans KR(SIL Open Font License) 글꼴의 윤곽선을 그대로 path로 옮겨, 글꼴이 없는 컴퓨터에서도 똑같이 보인다.
"""
from pathlib import Path

import pymupdf
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

OUT = Path(__file__).resolve().parent
FONT_BOLD = Path(r"C:\Windows\Fonts\NotoSansKR-Bold.ttf")
FONT_REG = Path(r"C:\Windows\Fonts\NotoSansKR-Regular.ttf")

DEEP = "#2E8B5E"      # 앞 잎(상담사 곁의 동료)
LIGHT = "#9ED3A8"     # 뒤 잎
INK = "#1E3A2C"       # 글자(밝은 배경)
INK_DARK = "#E8F3EA"  # 글자(어두운 배경)
TAG = "#5B7A68"
BG = "#EEF6EF"        # 앱 아이콘 배경

LEAF = "M0 0 C 19 -14, 19 -47, 0 -66 C -19 -47, -19 -14, 0 0 Z"


def mark_group(x=0, y=0, s=1.0, ring="#FFFFFF"):
    """120×120 좌표의 마크. 두 잎 끝이 위에서 맞닿아 서로 기댄 모양."""
    return f'''<g transform="translate({x} {y}) scale({s})">
  <path d="{LEAF}" transform="translate(84 90) rotate(-23)" fill="{LIGHT}"/>
  <path d="{LEAF}" transform="translate(36 90) rotate(23)" fill="{DEEP}" stroke="{ring}" stroke-width="3.5" stroke-linejoin="round"/>
  <path d="M0 -8 Q 3 -30 0 -50" transform="translate(36 90) rotate(23)" fill="none" stroke="#FFFFFF" stroke-opacity=".8" stroke-width="2.6" stroke-linecap="round"/>
  <path d="M0 -8 Q -3 -30 0 -50" transform="translate(84 90) rotate(-23)" fill="none" stroke="#FFFFFF" stroke-opacity=".85" stroke-width="2.6" stroke-linecap="round"/>
  <path d="M28 97 Q60 105 92 97" fill="none" stroke="{DEEP}" stroke-width="5" stroke-linecap="round"/>
</g>'''


def text_path(text, font_path, size, x, y, tracking=0.0):
    """글자를 SVG path로. (x, y)는 글자 기준선 시작점. 돌려주는 값: (path d, 전체 폭)."""
    font = TTFont(font_path)
    gs, cmap, upm = font.getGlyphSet(), font.getBestCmap(), font["head"].unitsPerEm
    k = size / upm
    parts, cx = [], x
    for ch in text:
        name = cmap.get(ord(ch))
        if name is None:
            continue
        pen = SVGPathPen(gs)
        gs[name].draw(TransformPen(pen, (k, 0, 0, -k, cx, y)))
        parts.append(pen.getCommands())
        cx += gs[name].width * k + tracking * size
    return " ".join(parts), cx - x


def svg(w, h, body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">\n{body}\n</svg>\n'


def render_png(svg_path, png_path, width):
    doc = pymupdf.open(svg_path)
    page = doc[0]
    zoom = width / page.rect.width
    page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True).save(png_path)


def main():
    files = {}
    # 1) 마크(투명 배경)
    files["gyeot_mark.svg"] = svg(120, 120, mark_group())
    # 2) 앱 아이콘(둥근 사각 배경)
    files["gyeot_icon.svg"] = svg(120, 120, f'<rect width="120" height="120" rx="28" fill="{BG}"/>\n'
                                  + mark_group(6, 4, 0.9, ring=BG))
    # 3) 가로 로고: 마크 + 곁 + 한 줄 소개 (밝은 배경 / 어두운 배경)
    for name, ink, tag in (("gyeot_logo.svg", INK, TAG), ("gyeot_logo_dark.svg", INK_DARK, "#A9C9B3")):
        word, ww = text_path("곁", FONT_BOLD, 84, 112, 92)
        sub, sw = text_path("상담실 곁의 사안처리 동료", FONT_REG, 19, 112 + ww + 14, 88)
        width = int(112 + ww + 14 + sw + 12)
        files[name] = svg(width, 120, mark_group(0, 0, 1.0, ring="#FFFFFF" if ink == INK else "#0E1512")
                          + f'\n<path d="{word}" fill="{ink}"/>\n<path d="{sub}" fill="{tag}"/>')
    # 4) 짧은 로고: 마크 + 곁(좁은 사이드바·제목용)
    word, ww = text_path("곁", FONT_BOLD, 84, 112, 92)
    files["gyeot_wordmark.svg"] = svg(int(112 + ww + 6), 120, mark_group() + f'\n<path d="{word}" fill="{INK}"/>')
    for name, body in files.items():
        (OUT / name).write_text(body, encoding="utf-8")
    render_png(OUT / "gyeot_icon.svg", OUT / "gyeot_icon_256.png", 256)
    render_png(OUT / "gyeot_icon.svg", OUT / "gyeot_icon_64.png", 64)
    render_png(OUT / "gyeot_mark.svg", OUT / "gyeot_mark_128.png", 128)
    render_png(OUT / "gyeot_logo.svg", OUT / "gyeot_logo.png", 720)
    render_png(OUT / "gyeot_logo_dark.svg", OUT / "gyeot_logo_dark.png", 720)
    render_png(OUT / "gyeot_wordmark.svg", OUT / "gyeot_wordmark.png", 400)
    print("만든 파일:", ", ".join(sorted(p.name for p in OUT.glob("gyeot_*"))))


if __name__ == "__main__":
    main()
