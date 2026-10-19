"""글자를 뽑을 수 없는 PDF(스캔본, 한글 문서를 '윤곽선'으로 인쇄한 PDF 등)를 OCR로 읽는다.

엔진 두 가지:
  gemini  : 쪽 이미지를 Gemini에 보내 글자를 옮겨 적게 한다. 표를 행 단위로 읽고 오탈자가 적다. 쪽당 20초 안팎, 6쪽씩 동시에.
            쪽 이미지가 Google Gemini API로 전송된다(학생 개인정보가 든 문서에는 쓰지 않는다).
  windows : Windows 기본 OCR(한국어). 이 PC 안에서만 처리하고 빠르다(쪽당 0.5초). 표·그림 속 글자 오탈자가 많다.
결과는 data/ocr/<PDF 내용 해시 32자>.json에 저장한다 → rag.page_texts()가 글자층이 없는 쪽을 이것으로 채운다.
같은 PDF는 이름을 바꿔 올려도 다시 OCR하지 않는다.
"""
import base64
import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(AGENT_DIR.parent))

import rag  # noqa: E402

ENGINES = {"gemini": "정확(Gemini, 쪽당 20초 안팎·6쪽씩 동시)", "windows": "빠름(Windows 기본 OCR, 이 PC 안에서만)"}
PS_SCRIPT = AGENT_DIR / "ocr_win.ps1"
GEMINI_PROMPT = ("이 PDF 쪽 이미지의 글자를 빠짐없이 그대로 옮겨 적어라. 읽는 순서대로 적고, 표는 행 단위로 '칸1 | 칸2'처럼 적는다. "
                 "설명·요약·추측을 덧붙이지 말고 이미지에 있는 글자만 출력한다. 글자가 없으면 빈 문자열을 출력한다.")


def text_coverage(pdf_path, min_chars=80):
    """글자층이 거의 없는 쪽 수와 전체 쪽 수. OCR이 필요한지 판단할 때 쓴다."""
    import pymupdf
    doc = pymupdf.open(pdf_path)
    low = sum(1 for p in doc if len(p.get_text().strip()) < min_chars)
    return low, len(doc)


def needs_ocr(pdf_path, ratio=0.5):
    low, n = text_coverage(pdf_path)
    return n > 0 and low / n >= ratio


def _render(doc, i, dpi):
    return doc[i].get_pixmap(dpi=dpi).tobytes("png")


def _gemini_page(png, retries=2):
    last = None
    for _ in range(retries + 1):
        try:
            r = rag.client().chat.completions.create(model=rag.LLM_MODEL, temperature=0, messages=[{"role": "user", "content": [
                {"type": "text", "text": GEMINI_PROMPT},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]}])
            return (r.choices[0].message.content or "").strip()
        except Exception as e:  # noqa: BLE001  (일시 오류는 다시 시도)
            last = e
    raise last


def _windows_batch(pngs):
    """{쪽 번호: png bytes} → {쪽 번호: 글자}. PowerShell 한 번에 여러 쪽."""
    with tempfile.TemporaryDirectory() as tmp:
        for i, png in pngs.items():
            Path(tmp, f"p{i:04d}.png").write_bytes(png)
        out = Path(tmp, "out.json")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS_SCRIPT), tmp, str(out)],
                       check=True, capture_output=True, timeout=600)
        data = json.loads(out.read_text(encoding="utf-8-sig"))
    return {int(k[1:5]): v for k, v in data.items()}


def run(pdf_path, engine="gemini", progress=None, workers=6):
    """PDF 전체를 OCR해 저장하고 결과를 돌려준다. 글자층이 이미 충분한 쪽은 건너뛴다.
    progress(끝난 쪽 수, OCR할 쪽 수)를 주면 진행 상황을 알린다."""
    import pymupdf
    doc = pymupdf.open(pdf_path)
    n = len(doc)
    todo = [i for i in range(n) if len(rag.clean_page_text(doc[i].get_text())) < rag.OCR_MIN_CHARS]
    texts = [""] * n
    failed, done = [], 0

    def tick():
        if progress:
            progress(done, len(todo))

    tick()
    if engine == "gemini":
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_gemini_page, _render(doc, i, 150)): i for i in todo}
            for f in as_completed(futs):
                i = futs[f]
                try:
                    texts[i] = f.result()
                except Exception:  # noqa: BLE001
                    failed.append(i)
                done += 1
                tick()
        if failed:  # Gemini가 끝내 못 읽은 쪽은 Windows OCR로 채운다
            try:
                for i, t in _windows_batch({i: _render(doc, i, 200) for i in failed}).items():
                    texts[i] = t
            except Exception:  # noqa: BLE001
                pass
    else:
        for s in range(0, len(todo), 10):
            batch = todo[s:s + 10]
            for i, t in _windows_batch({i: _render(doc, i, 200) for i in batch}).items():
                texts[i] = t
            done += len(batch)
            tick()
    result = {"engine": engine, "file": Path(pdf_path).name, "sha256": rag.file_sha256(pdf_path),
              "created_at": datetime.now().isoformat(timespec="seconds"), "pages_ocr": len(todo),
              "pages_failed": [i + 1 for i in failed if not texts[i]], "pages": texts}
    rag.OCR_DIR.mkdir(parents=True, exist_ok=True)
    (rag.OCR_DIR / f"{result['sha256'][:32]}.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    pdf, eng = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "gemini")
    r = run(pdf, eng, progress=lambda d, t: print(f"\r{d}/{t}", end="", flush=True))
    print(f"\n{r['pages_ocr']}쪽 OCR({eng}), 실패 {r['pages_failed']}")
