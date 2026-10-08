from __future__ import annotations

import csv
import io

from app.benchmark.config import METRICS
from app.benchmark.storage import atomic_file

USAGE_COLUMNS = (
    "answer_response_time_seconds",
    "answer_input_tokens",
    "answer_output_tokens",
    "answer_total_tokens",
)
RESULT_COLUMNS = ("question", *METRICS, *USAGE_COLUMNS)
ARTIFACT_EXTRAS = (
    *USAGE_COLUMNS,
    "generation_contexts",
    "generation_evidence_metadata",
    "conversation_history",
)


def result_rows(questions, checkpoint):
    for question in questions:
        state = checkpoint.get("items", {}).get(question["id"], {})
        if state.get("status") == "success":
            yield {**question, **state["result"]}


def result_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=RESULT_COLUMNS, delimiter=";", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def write_results(path, questions, checkpoint):
    with atomic_file(path, encoding="utf-8-sig", newline="", mode=0o640) as output:
        output.write(result_bytes(result_rows(questions, checkpoint)).decode("utf-8-sig"))
