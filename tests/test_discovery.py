import asyncio
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx

from hijenny import store
from hijenny.api.discovery import Criteria, Opportunity, RegistryScreen, fetch_screen, start_project


def test_filtered_page_preserves_registry_scope_and_continuation():
    criteria = Criteria(condition="melanoma", phase="PHASE2", results="Yes", intervention="Drug A", sponsor="Example", country="Japan")

    def respond(request):
        assert request.url.params["query.cond"] == "melanoma"
        assert request.url.params["query.intr"] == "Drug A"
        assert request.url.params["query.lead"] == "Example"
        assert request.url.params["filter.advanced"] == 'AREA[Phase]PHASE2 AND AREA[HasResults]Yes AND AREA[LocationCountry]"Japan"'
        assert request.url.params["pageToken"] == "cursor-1"
        return httpx.Response(200, json={"studies": [{"hasResults": True, "protocolSection": {
            "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Trial"},
            "statusModule": {"resultsFirstPostDateStruct": {"date": "2024-01-01"}},
            "contactsLocationsModule": {"locations": [{"country": "Japan"}, {"country": "Japan"}, {"country": "China"}, {}]},
            "armsInterventionsModule": {"interventions": [{"name": "Drug A", "otherNames": ["DA-01", "DA-01"]}, {"name": "Placebo"}]},
        }}], "nextPageToken": "cursor-2"})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await fetch_screen(client, RegistryScreen(criteria=criteria, cursor="cursor-1"))
        assert result["cursor"] == "cursor-2" and result["total"] is None
        assert result["items"][0]["has_results"] is True and result["items"][0]["results_posted"] == "2024-01-01"
        assert result["items"][0]["countries"] == ["China", "Japan"]
        assert result["items"][0]["intervention_aliases"] == [{"name": "Drug A", "aliases": ["DA-01"]}]
        assert result["items"][0]["interventions"] == ["Drug A", "Placebo"]
    asyncio.run(check())


def test_concurrent_handoff_reuses_project_and_retains_original_reason(tmp_path):
    store.configure(tmp_path / "discovery.db")
    store.init_db()
    seed = Opportunity(snapshot_id=uuid4(), retrieved="2026-11-26T00:00:00Z",
                       criteria=Criteria(intervention="Drug A", country="Japan"), countries=["Japan", "China"], nct="NCT00000001",
                       intervention_aliases=[{"name": "Drug A", "aliases": ["DA-01"]}], trial_title="Source record", asset="Drug A", rationale="First reason")
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(pool.map(start_project, [seed] * 3))
    assert sum(item["created"] for item in replies) == 1
    assert len({item["project"]["project_id"] for item in replies}) == 1
    restored = start_project(seed.model_copy(update={"rationale": "Later revision"}))["project"]
    assert restored["meta"]["opportunity"]["intervention_aliases"] == [{"name": "Drug A", "aliases": ["DA-01"]}]
    assert restored["meta"]["opportunity"]["countries"] == ["Japan", "China"]
    assert restored["meta"]["opportunity"]["criteria"]["intervention"] == "Drug A"
    assert restored["meta"]["opportunity"]["criteria"]["country"] == "Japan"
    assert restored["meta"]["opportunity"]["rationale"] == "First reason"
    assert restored["meta"]["opportunity"]["url"].endswith("/study/NCT00000001")
    assert start_project(seed.model_copy(update={"asset": "Drug B"}))["created"]
