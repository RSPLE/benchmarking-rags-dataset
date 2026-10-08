from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

from benchmark_storage import append_event, exclusive_lock, now, sanitize
from benchmark_usage import nonnegative_number, usage_records


def fetch_generation(identifier, role):
    key = (
        os.getenv(f"OPENROUTER_{role.upper()}_API_KEY")
        or (os.getenv("OPENROUTER_JUDGE_API_KEY") if role == "judge_embedding" else None)
        or os.getenv("OPENROUTER_API_KEY")
    )
    if not key:
        raise ValueError("OpenRouter credentials are missing")
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/generation?" + urllib.parse.urlencode({"id": identifier}),
        headers={"Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["data"]


def reconcile(root, *, apply=False, fetch=fetch_generation):
    root = Path(root).resolve()
    results = []
    with exclusive_lock(root / ".worker.lock"):
        for call_id, event in usage_records(root).items():
            if nonnegative_number(event.get("cost_usd")) is not None:
                continue
            result = {"call_id": call_id, "status": "unresolved"}
            identifier = event.get("provider_id")
            if not identifier or not identifier.startswith("gen-"):
                result["reason"] = "No supported provider generation ID; no automatic replay"
            elif not apply:
                result["status"] = "eligible"
            else:
                try:
                    data = fetch(identifier, event.get("role", "generation"))
                    cost = nonnegative_number(data.get("total_cost"))
                    if data.get("id") != identifier or cost is None:
                        raise ValueError("Provider metadata is incomplete or mismatched")
                    append_event(
                        root / "budget.jsonl",
                        {
                            **event,
                            "call_id": call_id,
                            "kind": "reconciled",
                            "at": now(),
                            "cost_usd": cost,
                            "consumption_unknown": False,
                            "reconciled_usage": {
                                key: value
                                for key, value in data.items()
                                if key.startswith(("tokens_", "native_tokens_"))
                            },
                        },
                    )
                    result.update(status="reconciled", cost_usd=cost)
                except Exception as exc:
                    result["reason"] = sanitize(str(exc))
            results.append(result)
    return {"applied": apply, "calls": results}
