import json

import app as dashboard


def test_checkpoint_summary_counts_and_averages(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "dataset_size", lambda: 3)
    result_dir = tmp_path / "self-rag"
    result_dir.mkdir()
    (result_dir / "checkpoint.json").write_text(
        json.dumps(
            {
                "updated_at": "2026-08-20T10:00:00Z",
                "items": {
                    "1": {"status": "success", "result": {"faithfulness": 0.8}},
                    "2": {"status": "success", "result": {"faithfulness": 1.0}},
                    "3": {"status": "failed"},
                },
            }
        ),
        encoding="utf-8",
    )

    summary = dashboard.checkpoint_summary("self-rag")

    assert summary["success"] == 2
    assert summary["failed"] == 1
    assert summary["pending"] == 0
    assert summary["progress"] == 66.7
    assert summary["metrics"]["faithfulness"] == 0.9


def test_missing_checkpoint_is_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "RESULTS_ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "dataset_size", lambda: 90)

    summary = dashboard.checkpoint_summary("context-rag")

    assert summary["pending"] == 90
    assert summary["progress"] == 0.0


def test_idle_reason_explains_that_pipeline_has_not_run():
    runner = {"available": True, "state": "idle", "logs": []}
    result = {"total": 90, "success": 0, "failed": 0}

    assert dashboard.display_state(runner, result) == "idle"
    assert "ainda não foi executado" in dashboard.state_reason(runner, result)


def test_cancelled_reason_is_explicit():
    runner = {
        "available": True,
        "state": "failed",
        "returncode": 143,
        "logs": ["Cancelamento solicitado pelo dashboard."],
    }
    result = {"total": 90, "success": 0, "failed": 0}

    assert "cancelada manualmente" in dashboard.state_reason(runner, result)


def test_missing_openrouter_key_reason_is_explicit():
    runner = {
        "available": True,
        "state": "failed",
        "returncode": 1,
        "logs": ["RuntimeError: OPENROUTER_API_KEY nao encontrada para o provedor openrouter."],
    }
    result = {"total": 90, "success": 0, "failed": 0}

    assert "OPENROUTER_API_KEY" in dashboard.state_reason(runner, result)
