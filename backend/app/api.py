"""HTTP routes."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse

from .agent.base import ResearchAgent
from .agent.pipeline import ResearchPipeline
from .config import Settings, get_settings
from .deps import ActiveResearch, get_active_research, get_agent, get_repository
from .repository import ReportRepository
from .schemas import Report, ResearchRequest, ReportSummary
from .usage import DailyUsage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Stops nginx and friends from buffering the stream into uselessness.
    "X-Accel-Buffering": "no",
}


@router.get("/health")
async def health(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    return {
        "status": "ok",
        "mode": "demo" if settings.demo_mode else "live",
        "provider": settings.provider,
    }


@router.post("/research")
async def research(
    payload: ResearchRequest,
    agent: ResearchAgent = Depends(get_agent),
    repository: ReportRepository = Depends(get_repository),
    active: ActiveResearch = Depends(get_active_research),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Stream a research run as Server-Sent Events.

    The report is saved only once the stream reaches `done`; a client that
    disconnects mid-run leaves nothing behind.
    """
    company = payload.company

    if not await active.acquire(company):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Research on {company} is already running.",
        )

    try:
        if not DailyUsage(repository.db).reserve(settings):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "daily_limit",
                    "message": "This app's daily research limit is reached. It resets at 00:00 UTC.",
                },
            )
    except Exception:
        await active.release(company)
        raise

    pipeline = ResearchPipeline(agent, repository)

    async def stream() -> AsyncIterator[str]:
        try:
            async for event in pipeline.run(company):
                yield event.encode()
        finally:
            # Runs on completion, on error, and when the client disconnects and
            # the generator is closed -- so the company never stays locked.
            await active.release(company)

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/usage")
async def usage(
    settings: Settings = Depends(get_settings),
    repository: ReportRepository = Depends(get_repository),
) -> dict:
    return DailyUsage(repository.db).snapshot(settings)


@router.get("/reports", response_model=list[ReportSummary])
async def list_reports(repository: ReportRepository = Depends(get_repository)):
    return repository.list()


@router.get("/reports/{report_id}", response_model=Report)
async def get_report(report_id: int, repository: ReportRepository = Depends(get_repository)):
    report = repository.get(report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That report no longer exists.")
    return report


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(report_id: int, repository: ReportRepository = Depends(get_repository)):
    if not repository.delete(report_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That report no longer exists.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
