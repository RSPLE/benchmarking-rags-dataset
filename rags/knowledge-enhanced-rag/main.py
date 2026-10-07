import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pathlib import Path

from rag_settings import (
    configure_environment,
    finish_usage_tracker,
    start_usage_tracker,
)

from benchmark_pipeline import execute_pipeline

configure_environment("benchmark-knowledge-enhanced-rag")

from src.chatbot import Chatbot


def prepare():
    chatbot = Chatbot()

    def answer_question(item):
        tracker, started_at = start_usage_tracker()
        result = chatbot.chat(item["question"], session_id=None, callbacks=[tracker])
        return {
            "question": item["question"],
            "answer": result["answer"],
            "contexts": result["contexts"],
            "ground_truth": item["ground_truth"],
            "evidence_metadata": result["evidence_metadata"],
            **finish_usage_tracker(tracker, started_at),
        }

    return answer_question


def evaluate_ke_rag():
    return execute_pipeline("knowledge-enhanced-rag", prepare)


if __name__ == "__main__":
    evaluate_ke_rag()
