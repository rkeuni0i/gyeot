"""저장소(index/mindguide.db)의 자료 청크를 PDF에서 만들고 임베딩한다.

저장소가 없으면 먼저 기존 index/chunks.json·embeddings.npy에서 옮겨 만든다(rag.refresh_docs).
기본: 청크가 없는 활성 자료만 만든다. PDF를 같은 이름으로 바꿨거나 청크 규칙을 바꿨으면 --all 또는 doc_id를 준다.
임베딩은 Gemini API를 호출한다.

실행:  C:\\Users\\2003g\\anaconda3\\envs\\DS\\python.exe build_index.py [--all | doc_id ...]
"""
import sys
import time

import rag
import store
from doc_agent import ingest

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    t = time.time()
    rag.refresh_docs()
    con = store.connect()
    counts = store.chunk_counts(con)
    con.close()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    targets = args or (list(rag.DOCS) if "--all" in sys.argv else [d for d in rag.DOCS if not counts.get(d)])
    if not targets:
        print("모든 활성 자료에 청크가 있습니다. 다시 만들려면 --all 또는 doc_id를 주세요.")
    for d in targets:
        n = ingest.reindex_document(d)
        print(f"[{rag.DOCS[d]['short']}] 청크 {n}개")
    print(f"완료 [{time.time() - t:.1f}초] → {rag.DB_PATH}")
