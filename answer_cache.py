"""자주 묻는 질문 답변 캐시(logs/answer_cache.json, 이 컴퓨터에만 저장).

같은 질문을 다시 하면 Gemini를 부르지 않고 저장된 답을 바로 보여 준다(약 25초 → 1초 안).
- 대화의 첫 질문만 캐시한다. 후속 질문은 이전 대화에 따라 뜻이 달라지기 때문이다.
- 열쇠 = 정리한 질문 글자 + 자료 구성 지문(Index.fingerprint) + 규칙·모델 지문.
  자료를 추가·교체·사용 중지하거나 답변 규칙·검색 설정을 바꾸면 열쇠가 달라져 예전 답은 쓰이지 않는다.
- 화면에서 '새로 답변 받기'를 누르면 캐시를 건너뛰고 다시 만든 답으로 바꾼다.
"""
import hashlib
import json
import re
import time
from pathlib import Path

import rag

PATH = Path(__file__).resolve().parent / "logs" / "answer_cache.json"
MAX_ITEMS = 300


def _norm(question):
    q = " ".join(question.split())
    return re.sub(r"[\s?!.~…]+$", "", q)


def _rules_fingerprint():
    spec = json.dumps({"system": rag.system_prompt(), "query": rag.QUERY_PROMPT, "model": rag.LLM_MODEL,
                       "formats": {k: v["format"] for k, v in rag.QTYPES.items()}, "retrieval": rag.RETRIEVAL,
                       "max_chunks": rag.MAIN_MAX_CHUNKS, "must": rag.MUST_QUERIES}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(spec.encode()).hexdigest()[:16]


def key(question, index):
    raw = f"{_norm(question)}|{getattr(index, 'fingerprint', '')}|{_rules_fingerprint()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _load():
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def get(question, index):
    """저장된 결과(finish 결과 + cached_at) 또는 None."""
    item = _load().get(key(question, index))
    return item["result"] if item else None


def put(question, index, result):
    data = _load()
    res = {**result, "cached_at": time.strftime("%Y-%m-%d %H:%M")}
    data[key(question, index)] = {"question": _norm(question), "saved_at": time.time(), "result": res}
    if len(data) > MAX_ITEMS:  # 오래된 것부터 지운다
        for k in sorted(data, key=lambda k: data[k]["saved_at"])[:len(data) - MAX_ITEMS]:
            del data[k]
    PATH.parent.mkdir(exist_ok=True)

    def plain(o):
        return o.item() if hasattr(o, "item") else list(o) if isinstance(o, (set, tuple)) else str(o)
    PATH.write_text(json.dumps(data, ensure_ascii=False, default=plain), encoding="utf-8")


def clear():
    if PATH.exists():
        PATH.unlink()


def stats():
    data = _load()
    return {"items": len(data), "path": str(PATH)}
