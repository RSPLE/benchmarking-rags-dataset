from __future__ import annotations

import json
import os
from pathlib import Path

from benchmark_config import configuration, corpus_inventory
from benchmark_storage import atomic_json, exclusive_lock, fingerprint


def chunk_ids(chunks):
    return [
        fingerprint({"position": i, "text": doc.page_content, "metadata": doc.metadata})
        for i, doc in enumerate(chunks)
    ]


def index_dimension(store):
    values = store.get(include=["embeddings"], limit=1).get("embeddings")
    if values is None or len(values) == 0:
        return None
    return len(values[0])


def ensure_index(store, chunks, manifest_path, identity):
    manifest_path = Path(manifest_path)
    expected = chunk_ids(chunks)
    if not expected:
        raise ValueError("No usable chunks in corpus")
    with exclusive_lock(manifest_path.with_suffix(".lock")):
        existing = set(store.get(include=[])["ids"])
        dimension = index_dimension(store) if existing else None
        expected_dimension = int(os.getenv("EMBEDDING_DIMENSIONS", "0"))
        if expected_dimension and dimension and dimension != expected_dimension:
            raise RuntimeError("Stored vector dimension differs from configured embeddings")
        if manifest_path.exists():
            saved = json.loads(manifest_path.read_text())
            if saved.get("embedding_dimension") and dimension != saved["embedding_dimension"]:
                raise RuntimeError("Stored vector dimension differs from the index manifest")
            if saved.get("identity") != identity or saved.get("ids") != expected:
                raise RuntimeError(
                    "Index manifest mismatch; preserve the existing index and use a new path"
                )
        elif existing:
            raise RuntimeError(
                "Unverified legacy index; preserve it and use a new CHROMA_PERSIST_DIR"
            )
        if existing - set(expected):
            raise RuntimeError("Unexpected chunks in index")
        atomic_json(manifest_path, {"identity": identity, "ids": expected, "complete": False})
        missing = [
            (doc, identifier)
            for doc, identifier in zip(chunks, expected, strict=True)
            if identifier not in existing
        ]
        for offset in range(0, len(missing), 64):
            batch = missing[offset : offset + 64]
            store.add_documents(documents=[doc for doc, _ in batch], ids=[key for _, key in batch])
        if set(store.get(include=[])["ids"]) != set(expected):
            raise RuntimeError("Incomplete ingestion")
        atomic_json(
            manifest_path,
            {
                "identity": identity,
                "ids": expected,
                "complete": True,
                "embedding_dimension": index_dimension(store),
            },
        )


def load_index(docs_dir, persist_dir, collection, *, chunk_size=800, chunk_overlap=100):
    from langchain_chroma import Chroma
    from langchain_community.document_loaders import PyPDFLoader
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    from rag_provider import build_embeddings

    inventory = corpus_inventory(docs_dir)
    if not inventory or any(entry["bytes"] == 0 for entry in inventory):
        raise ValueError(f"Missing or empty PDFs: {docs_dir}")
    pages = []
    for entry in inventory:
        extracted = PyPDFLoader(str(Path(docs_dir) / entry["path"])).load()
        usable = [page for page in extracted if page.page_content.strip()]
        if not usable:
            raise ValueError(f"PDF contains no usable text: {entry['path']}")
        for page in usable:
            page.metadata["source"] = entry["path"]
            page.metadata["source_sha256"] = entry["sha256"]
        pages.extend(usable)
    chunks = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap, add_start_index=True
    ).split_documents(pages)
    config = configuration()
    identity = fingerprint(
        {
            "corpus": inventory,
            "size": chunk_size,
            "overlap": chunk_overlap,
            "embeddings": {key: value for key, value in config.items() if "EMBEDDING" in key},
            "endpoint": config["OPENROUTER_BASE_URL"],
            "schema": 2,
        }
    )
    persist_dir = Path(persist_dir)
    embeddings = build_embeddings()
    store = Chroma(
        collection_name=collection,
        embedding_function=embeddings,
        persist_directory=str(persist_dir),
    )
    ensure_index(store, chunks, persist_dir / f"{collection}.manifest.json", identity)
    return store, embeddings, chunks


def cached_extraction(path, identity, extract):
    path = Path(path)
    if path.exists():
        saved = json.loads(path.read_text())
        if saved.get("identity") != identity:
            raise ValueError("Graph extraction identity mismatch")
        result = saved["result"]
        if saved.get("result_sha256") != fingerprint(result):
            raise ValueError("Graph extraction content changed")
    else:
        result = extract()
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("entities"), list)
        or not isinstance(result.get("relations"), list)
    ):
        raise ValueError("Malformed graph extraction")
    ids = set()
    for entity in result["entities"]:
        if not isinstance(entity, dict) or any(
            not isinstance(entity.get(key), str) or not entity[key].strip()
            for key in ("id", "name", "type")
        ):
            raise ValueError("Malformed graph entity")
        if entity["id"] in ids:
            raise ValueError("Duplicate graph entity ID")
        ids.add(entity["id"])
    for relation in result["relations"]:
        if (
            not isinstance(relation, dict)
            or relation.get("source") not in ids
            or relation.get("target") not in ids
        ):
            raise ValueError("Malformed graph relation")
    if not path.exists():
        atomic_json(
            path, {"identity": identity, "result": result, "result_sha256": fingerprint(result)}
        )
    return result
