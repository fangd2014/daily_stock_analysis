"""Quantitative strategy catalog and execution endpoints."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from api.v1.schemas.quant import QuantRunRequest
from src.services.quant_service import QuantRunConflictError, get_quant_strategy_service


router = APIRouter()


@router.get("/strategies", summary="List registered quantitative strategies")
def list_strategies() -> dict:
    """Return strategy logic, public parameters, latest outputs, and run state."""
    service = get_quant_strategy_service()
    strategies = service.list_strategies()
    return {"total": len(strategies), "strategies": strategies}


@router.get("/strategies/{strategy_id}", summary="Get one quantitative strategy")
def get_strategy(strategy_id: str) -> dict:
    """Return complete detail for one registered strategy."""
    strategy = get_quant_strategy_service().get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail={"error": "strategy_not_found", "message": "策略不存在"})
    return strategy


@router.post("/runs", status_code=202, summary="Start a quantitative strategy run")
def start_run(request: QuantRunRequest) -> JSONResponse:
    """Start one allow-listed strategy process without exposing arbitrary commands."""
    service = get_quant_strategy_service()
    try:
        run = service.start_run(request.strategy_id)
    except KeyError:
        raise HTTPException(status_code=404, detail={"error": "strategy_not_found", "message": "策略不存在"})
    except QuantRunConflictError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "error": "strategy_running",
                "message": "该策略已有执行中的任务",
                "strategy_id": exc.strategy_id,
                "run_id": exc.run_id,
            },
        )
    return JSONResponse(status_code=202, content=run.to_dict())


@router.get("/runs", summary="List recent quantitative strategy runs")
def list_runs(limit: int = Query(20, ge=1, le=50)) -> dict:
    """Return recent run summaries without full logs."""
    runs = [run.to_dict(include_logs=False) for run in get_quant_strategy_service().list_runs(limit)]
    return {"total": len(runs), "runs": runs}


@router.get("/runs/{run_id}", summary="Get quantitative strategy run state")
def get_run(run_id: str) -> dict:
    """Return status, ordered process logs, and normalized results for one run."""
    run = get_quant_strategy_service().get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"error": "run_not_found", "message": "执行记录不存在"})
    return run.to_dict()
