import logging
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers.agent import router as agent_router
from app.api.routers.apps import router as apps_router
from app.api.routers.graph import router as graph_router
from app.api.routers.health import router as health_router
from app.api.routers.lineage import router as lineage_router
from app.api.routers.parser import router as parser_router
from app.api.routers.realtime import router as realtime_router
from app.api.routers.scan import router as scan_router
from app.api.routers.search import router as search_router
from app.config import settings
from app.dependencies import get_neo4j, get_repository

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Qlik Lineage Copilot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(parser_router)
app.include_router(lineage_router)
app.include_router(agent_router)
app.include_router(scan_router)
app.include_router(search_router)
app.include_router(graph_router)
app.include_router(apps_router)
app.include_router(realtime_router)


@app.on_event("startup")
def _startup() -> None:
    try:
        get_repository().init_schema()
    except Exception as exc:  # pragma: no cover - non-fatal during local dev
        logging.getLogger(__name__).warning("Postgres schema init skipped: %s", exc)
    try:
        get_neo4j().init_schema()
    except Exception as exc:  # pragma: no cover - non-fatal during local dev
        logging.getLogger(__name__).warning("Neo4j schema init skipped: %s", exc)
    _start_scheduler()


def _start_scheduler() -> None:
    """Run a periodic delta scan when QLIK_SCAN_INTERVAL_SECONDS is set.

    Keeps lineage current without anyone remembering to trigger a scan. Disabled by
    default (0) so nothing runs automatically unless explicitly configured.
    """
    interval = int(getattr(settings, "scan_interval_seconds", 0) or 0)
    log = logging.getLogger(__name__)
    if interval <= 0:
        log.info("Background scan scheduler disabled (scan_interval_seconds=0)")
        return

    import threading

    from app.dependencies import get_orchestrator

    def _loop() -> None:
        while True:
            time.sleep(interval)
            try:
                log.info("Scheduled delta scan starting")
                result = get_orchestrator().run(mode="delta")
                log.info("Scheduled delta scan finished: %s", result.get("stats"))
            except Exception:  # noqa: BLE001 - a failed scan must not kill the loop
                log.exception("Scheduled delta scan failed; will retry next interval")

    threading.Thread(target=_loop, name="lineage-delta-scan", daemon=True).start()
    log.info("Background delta scan scheduled every %d seconds", interval)


@app.on_event("shutdown")
def _shutdown() -> None:
    try:
        get_neo4j().close()
    except Exception:  # pragma: no cover
        pass
