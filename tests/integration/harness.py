"""In-process HTTP harness for the four real private Agent applications."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager

import httpx
from hennongxi_analysis_agent.artifacts import AnalysisArtifactStore
from hennongxi_analysis_agent.execution import AnalysisExecutor
from hennongxi_analysis_agent.main import app as analysis_app
from hennongxi_data_agent.main import app as data_app
from hennongxi_data_agent.preparation import DataPreparer
from hennongxi_publisher_agent.catalog import PublisherArtifactCatalog
from hennongxi_publisher_agent.main import app as publisher_app
from hennongxi_publisher_agent.publication import PublicationService
from hennongxi_publisher_agent.report_artifacts import ReportArtifactStore
from hennongxi_quality_agent.artifacts import QualityArtifactStore
from hennongxi_quality_agent.execution import QualityExecutor
from hennongxi_quality_agent.main import app as quality_app

from tests.fixtures.deterministic_gis import DeterministicGisFixture


@contextmanager
def configured_agent_apps(fixture: DeterministicGisFixture) -> Iterator[None]:
    """Point all Agent app state at one isolated fixture and restore it afterwards."""

    previous = {
        "data_preparer": data_app.state.data_preparer,
        "analysis_executor": analysis_app.state.analysis_executor,
        "quality_executor": quality_app.state.quality_executor,
        "publisher_catalog": publisher_app.state.publisher_catalog,
        "report_store": publisher_app.state.report_store,
        "publication_service": publisher_app.state.publication_service,
    }
    analysis_store = AnalysisArtifactStore(fixture.artifact_root)
    quality_store = QualityArtifactStore(fixture.quality_report_root)
    publisher_catalog = PublisherArtifactCatalog(
        fixture.artifact_root,
        fixture.quality_report_root,
    )
    report_store = ReportArtifactStore(fixture.artifact_root)
    data_app.state.data_preparer = DataPreparer(
        fixture.manifest_path,
        data_root=fixture.data_root,
        cache_dir=fixture.cache_dir,
    )
    analysis_app.state.analysis_executor = AnalysisExecutor(
        fixture.manifest_path,
        data_root=fixture.data_root,
        cache_dir=fixture.cache_dir,
        artifact_store=analysis_store,
    )
    quality_app.state.quality_executor = QualityExecutor(
        fixture.manifest_path,
        analysis_artifact_root=fixture.artifact_root,
        report_store=quality_store,
    )
    publisher_app.state.publisher_catalog = publisher_catalog
    publisher_app.state.report_store = report_store
    publisher_app.state.publication_service = PublicationService(
        publisher_catalog,
        fixture.manifest_path,
        report_store,
    )
    try:
        yield
    finally:
        data_app.state.data_preparer = previous["data_preparer"]
        analysis_app.state.analysis_executor = previous["analysis_executor"]
        quality_app.state.quality_executor = previous["quality_executor"]
        publisher_app.state.publisher_catalog = previous["publisher_catalog"]
        publisher_app.state.report_store = previous["report_store"]
        publisher_app.state.publication_service = previous["publication_service"]


class _RoutedAgentTransport(httpx.AsyncBaseTransport):
    """Route fixed test origins to ASGI apps without opening a network socket."""

    def __init__(self) -> None:
        self._routes = {
            "data.test": httpx.ASGITransport(app=data_app, raise_app_exceptions=False),
            "analysis.test": httpx.ASGITransport(app=analysis_app, raise_app_exceptions=False),
            "quality.test": httpx.ASGITransport(app=quality_app, raise_app_exceptions=False),
            "publisher.test": httpx.ASGITransport(app=publisher_app, raise_app_exceptions=False),
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = self._routes.get(request.url.host)
        if transport is None:
            raise httpx.ConnectError("unrouted integration-test origin", request=request)
        return await transport.handle_async_request(request)

    async def aclose(self) -> None:
        await _close_all(self._routes.values())


async def _close_all(transports: Iterable[httpx.AsyncBaseTransport]) -> None:
    for transport in transports:
        await transport.aclose()


def routed_agent_transport() -> httpx.AsyncBaseTransport:
    return _RoutedAgentTransport()
