"""Regression tests for the quantitative strategy workspace API."""

import sys

from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from api.app import app
from src.services.quant_service import (
    QuantLogEntry,
    QuantRun,
    QuantRunConflictError,
    QuantStrategyService,
    get_quant_strategy_service,
)


client = TestClient(app)


def test_quant_strategy_catalog_exposes_registered_strategies_without_credentials() -> None:
    response = client.get("/api/v1/quant/strategies")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert {item["strategy_id"] for item in payload["strategies"]} == {
        "chip-double-peak",
        "hot-sector-commanders",
        "four-factor",
        "etf-momentum",
    }
    serialized = response.text.lower()
    assert "api_key_env" not in serialized
    assert "webhook_url" not in serialized


def test_quant_commands_are_allow_listed_argument_arrays() -> None:
    service = get_quant_strategy_service()

    for spec in service.specs.values():
        command = spec.command_factory(service.root)
        assert isinstance(command, list)
        assert command[0] == sys.executable
        assert command[1:3] == ["-u", "-m"]
        assert command[3].startswith("src.quant.")
        assert "-c" not in command
        assert all(isinstance(argument, str) for argument in command)


def test_quant_execution_captures_logs_and_refreshes_result(tmp_path, monkeypatch) -> None:
    service = object.__new__(QuantStrategyService)
    service._initialized = False
    service.__init__(root=tmp_path, max_workers=1)
    run = QuantRun(
        run_id="run-lifecycle",
        strategy_id="etf-momentum",
        strategy_name="ETF动量轮动",
    )
    service._runs[run.run_id] = run
    service._active_by_strategy[run.strategy_id] = run.run_id

    class FakeProcess:
        stdout = iter(["2026-07-25 INFO data loaded\n", "2026-07-25 INFO selection completed\n"])

        @staticmethod
        def wait() -> int:
            return 0

    monkeypatch.setattr("src.services.quant_service.subprocess.Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(service, "_load_latest_result", lambda spec: {"selected_count": 3, "selections": []})

    service._execute_run(run.run_id)

    completed = service.get_run(run.run_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.progress == 100
    assert completed.result == {"selected_count": 3, "selections": []}
    assert any("data loaded" in entry.message for entry in completed.logs)
    assert run.strategy_id not in service._active_by_strategy
    service._executor.shutdown(wait=True)


def test_start_quant_run_returns_accepted_state() -> None:
    fake_service = Mock()
    fake_service.start_run.return_value = QuantRun(
        run_id="run-1",
        strategy_id="etf-momentum",
        strategy_name="ETF动量轮动",
        logs=[QuantLogEntry(1, "2026-07-25T20:00:00+08:00", "INFO", "Strategy run queued")],
    )

    with patch("api.v1.endpoints.quant.get_quant_strategy_service", return_value=fake_service):
        response = client.post("/api/v1/quant/runs", json={"strategy_id": "etf-momentum"})

    assert response.status_code == 202
    assert response.json()["run_id"] == "run-1"


def test_duplicate_quant_run_returns_conflict_and_existing_run() -> None:
    fake_service = Mock()
    fake_service.start_run.side_effect = QuantRunConflictError("etf-momentum", "active-run")

    with patch("api.v1.endpoints.quant.get_quant_strategy_service", return_value=fake_service):
        response = client.post("/api/v1/quant/runs", json={"strategy_id": "etf-momentum"})

    assert response.status_code == 409
    assert response.json()["run_id"] == "active-run"
