import asyncio
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx

from hijenny import store
from hijenny.api.discovery import Criteria, Opportunity, RegistryScreen, fetch_screen, start_project


def test_filtered_page_preserves_registry_scope_and_continuation():
    criteria = Criteria(condition="melanoma", phase="PHASE2", sponsor="Example")

    def respond(request):
        assert request.url.params["query.cond"] == "melanoma"
        assert request.url.params["query.lead"] == "Example"
        assert request.url.params["filter.advanced"] == "AREA[Phase]PHASE2"
        assert request.url.params["pageToken"] == "cursor-1"
        return httpx.Response(200, json={"studies": [{"protocolSection": {
            "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Trial"},
            "armsInterventionsModule": {"interventions": [{"name": "Drug A"}, {"name": "Placebo"}]},
        }}], "nextPageToken": "cursor-2"})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await fetch_screen(client, RegistryScreen(criteria=criteria, cursor="cursor-1"))
        assert result["cursor"] == "cursor-2" and result["total"] is None
        assert result["items"][0]["interventions"] == ["Drug A", "Placebo"]
    asyncio.run(check())


def test_concurrent_handoff_reuses_project_and_retains_original_reason(tmp_path):
    store.configure(tmp_path / "discovery.db")
    store.init_db()
    seed = Opportunity(snapshot_id=uuid4(), retrieved="2026-11-26T00:00:00Z",
                       criteria=Criteria(condition="example"), nct="NCT00000001",
                       trial_title="Source record", asset="Drug A", rationale="First reason")
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(pool.map(start_project, [seed] * 3))
    assert sum(item["created"] for item in replies) == 1
    assert len({item["project"]["project_id"] for item in replies}) == 1
    restored = start_project(seed.model_copy(update={"rationale": "Later revision"}))["project"]
    assert restored["meta"]["opportunity"]["rationale"] == "First reason"
    assert restored["meta"]["opportunity"]["url"].endswith("/study/NCT00000001")
    assert start_project(seed.model_copy(update={"asset": "Drug B"}))["created"]
