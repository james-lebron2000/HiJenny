"""Public-source contract tests run offline with recorded-shape fixtures."""
import asyncio

import httpx
import pytest
from fastapi import HTTPException

from hijenny.api.research import SearchRequest, pubmed, search_public_records, trials


def test_pubmed_keeps_abstract_labels_and_inline_text():
    def respond(request):
        if request.url.path.endswith("/esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"count": "1", "idlist": ["123"]}})
        assert request.url.params["id"] == "123"
        return httpx.Response(200, text="""<PubmedArticleSet><PubmedArticle><MedlineCitation>
            <PMID>123</PMID><Article><ArticleTitle>A <i>study</i></ArticleTitle>
            <AuthorList><Author><LastName>Example</LastName><Initials>AB</Initials></Author></AuthorList>
            <Abstract><AbstractText Label="RESULTS">Evidence <i>here</i>.</AbstractText></Abstract>
            </Article></MedlineCitation></PubmedArticle></PubmedArticleSet>""")

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            total, items = await pubmed(client, "study")
            assert total == 1 and items[0]["title"] == "A study"
            assert items[0]["authors"] == ["AB Example"]
            assert items[0]["passages"] == [{"label": "RESULTS", "text": "Evidence here."}]
    asyncio.run(check())


def test_empty_trial_search():
    def respond(request):
        assert request.url.params["query.term"] == "example"
        return httpx.Response(200, json={"totalCount": 0, "studies": []})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            assert await trials(client, "example") == (0, [])
    asyncio.run(check())


def test_whitespace_is_rejected_before_network_access():
    with pytest.raises(HTTPException) as error:
        asyncio.run(search_public_records(SearchRequest(source="pubmed", query="   ")))
    assert error.value.status_code == 422


def test_trial_comparison_preserves_zero_and_missing_enrollment():
    def respond(request):
        studies = []
        for n, enrollment in enumerate([{"count": 0, "type": "ACTUAL"}, {}]):
            studies.append({"protocolSection": {
                "identificationModule": {"nctId": f"NCT0000000{n}", "briefTitle": "Example"},
                "statusModule": {"overallStatus": "TERMINATED"},
                "designModule": {"enrollmentInfo": enrollment},
                "outcomesModule": {"primaryOutcomes": [{"measure": "Example endpoint", "timeFrame": "1 year"}]},
            }})
        return httpx.Response(200, json={"totalCount": 2, "studies": studies})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            _, items = await trials(client, "example")
            first, second = [item["comparison"] for item in items]
            assert first["入组人数"] == 0 and second["入组人数"] is None
            assert first["入组数类型"] == "ACTUAL"
            assert first["登记主要终点"] == ["Example endpoint · 1 year"]
    asyncio.run(check())


def test_exact_trial_identifier_uses_detail_and_preserves_date_precision():
    def respond(request):
        assert request.url.path.endswith("/studies/NCT00000001")
        return httpx.Response(200, json={"protocolSection": {
            "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Example"},
            "statusModule": {"overallStatus": "RECRUITING", "completionDateStruct": {
                "date": "2028-03", "type": "ESTIMATED"}},
        }})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            count, rows = await trials(client, "nct00000001")
            assert count == 1
            assert rows[0]["milestones"]["研究完成日期"] == {"date": "2028-03", "type": "ESTIMATED"}
    asyncio.run(check())
