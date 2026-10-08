import json
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rag_settings import (
    configure_environment,
    finish_usage_tracker,
    start_usage_tracker,
)

from benchmark_pipeline import execute_pipeline

configure_environment("benchmark-knowledge-enhanced-rag")

from langchain_core.messages import AIMessage, HumanMessage
from src.chatbot import Chatbot


def restore_history(chatbot, checkpoint):
    if checkpoint is None:
        return
    chatbot.memorias["benchmark"] = deque(maxlen=10)
    if not checkpoint.is_file():
        return
    saved = json.loads(checkpoint.read_text())
    items = [item for item in saved["items"].values() if "artifact" in item]
    if not items:
        return
    latest = max(items, key=lambda item: item.get("generation_order", 0))
    history = latest["artifact"].get("conversation_history")
    if not isinstance(history, list) or len(history) > 10 or len(history) % 2:
        raise ValueError("Saved Knowledge conversation history is missing or invalid")
    messages = []
    for index, message in enumerate(history):
        expected = "human" if index % 2 == 0 else "ai"
        if message.get("role") != expected or not isinstance(message.get("content"), str):
            raise ValueError("Invalid Knowledge conversation message")
        factory = HumanMessage if expected == "human" else AIMessage
        messages.append(factory(content=message["content"]))
    chatbot.memorias["benchmark"] = deque(messages, maxlen=10)


def prepare(checkpoint=None):
    chatbot = Chatbot()

    def answer_question(item):
        restore_history(chatbot, checkpoint)
        tracker, started_at = start_usage_tracker()
        result = chatbot.chat(item["question"], session_id="benchmark", callbacks=[tracker])
        return {
            "question": item["question"],
            "answer": result["answer"],
            "contexts": result["contexts"],
            "ground_truth": item["ground_truth"],
            "evidence_metadata": result["evidence_metadata"],
            "generation_contexts": result["generation_contexts"],
            "generation_evidence_metadata": result["generation_evidence_metadata"],
            "conversation_history": result["conversation_history"],
            **finish_usage_tracker(tracker, started_at),
        }

    return answer_question


def evaluate_ke_rag():
    return execute_pipeline("knowledge-enhanced-rag", prepare)


if __name__ == "__main__":
    evaluate_ke_rag()
