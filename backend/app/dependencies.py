from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.agent.tools import AgentTools
from app.agent.workflow import LineageCopilotAgent
from app.config import settings
from app.connectors.engine_client import EngineClient
from app.connectors.qrs_client import QrsClient
from app.graph.neo4j_client import Neo4jClient
from app.lineage.builder import LineageBuilder
from app.parser.qlik_parser import QlikScriptParser
from app.scanner.orchestrator import ScannerOrchestrator
from app.storage.repository import PostgresRepository


@lru_cache
def get_repository() -> PostgresRepository:
    return PostgresRepository(dsn=settings.postgres_dsn)


@lru_cache
def get_neo4j() -> Neo4jClient:
    return Neo4jClient(uri=settings.neo4j_uri, user=settings.neo4j_user, password=settings.neo4j_password)


@lru_cache
def get_qrs() -> QrsClient:
    return QrsClient(
        base_url=settings.qlik_base_url,
        qlik_user=settings.qlik_user,
        xrf_key=settings.qlik_xrf_key,
        verify_ssl=settings.qlik_verify_ssl,
        timeout_seconds=settings.qlik_timeout_seconds,
    )


@lru_cache
def get_engine() -> EngineClient:
    certs_dir = Path(__file__).resolve().parent.parent / "certs"
    return EngineClient(
        base_url=settings.qlik_base_url,
        qlik_user=settings.qlik_engine_user,
        engine_host=settings.qlik_engine_host,
        engine_port=settings.qlik_engine_port,
        engine_path=settings.qlik_engine_path,
        client_pfx=settings.qlik_client_pfx or str(certs_dir / "client.pfx"),
        pfx_password=settings.qlik_pfx_password,
        root_cer=settings.qlik_root_cer or str(certs_dir / "root.cer"),
        verify_ssl=settings.qlik_verify_ssl,
        timeout_seconds=settings.qlik_engine_timeout_seconds,
        export_scripts=settings.qlik_export_scripts,
        export_scripts_dir=settings.qlik_export_scripts_dir,
    )


@lru_cache
def get_parser() -> QlikScriptParser:
    return QlikScriptParser()


@lru_cache
def get_builder() -> LineageBuilder:
    return LineageBuilder()


@lru_cache
def get_orchestrator() -> ScannerOrchestrator:
    return ScannerOrchestrator(
        qrs=get_qrs(),
        engine=get_engine(),
        repository=get_repository(),
        neo4j=get_neo4j(),
        parser=get_parser(),
        builder=get_builder(),
        max_concurrent_apps=settings.qlik_max_concurrent_apps,
    )


@lru_cache
def get_agent() -> LineageCopilotAgent:
    return LineageCopilotAgent(
        tools=AgentTools(repository=get_repository(), neo4j=get_neo4j())
    )
