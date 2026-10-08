from __future__ import annotations

import os


def select_documents(documents):
    limit = int(os.getenv("BENCHMARK_CONTEXT_MAX_BYTES", "32000"))
    if limit < 1:
        raise ValueError("BENCHMARK_CONTEXT_MAX_BYTES must be positive")
    selected, seen, used = [], set(), 0
    for document in documents:
        text = document.page_content
        if text in seen:
            continue
        seen.add(text)
        size = len(text.encode("utf-8"))
        if used + size > limit:
            continue
        selected.append(document)
        used += size
    if not selected:
        raise ValueError("No retrieved document fits the context budget")
    return selected
