from io import BytesIO
from uuid import uuid4

import pytest
from pydantic import ValidationError
from pptx import Presentation

from hijenny.api.medical import Meeting, export_meeting


def test_confirmed_slides_paginate_evidence_and_link_snapshot():
    quote = "证据原文" * 150
    data = dict(id=uuid4(), briefId=uuid4(), confirmed=True, created="2026-11-26",
                question="待讨论问题", reviewer="审核人", answer="已确认答复",
                refs=[dict(id="123", title="文献标题", label="摘要", quote=quote)])
    deck = Presentation(BytesIO(export_meeting(Meeting(**data)).body))
    bodies = [slide.shapes[1].text.replace("\n", "") for slide in deck.slides]
    assert quote in "".join(bodies) and data["answer"] in bodies
    linked = [s for s in deck.slides if s.shapes[0].text.startswith("支持引文")]
    assert len(linked) > 1
    for slide in linked:
        assert slide.shapes[2].text_frame.paragraphs[0].runs[-1].hyperlink.address == "https://pubmed.ncbi.nlm.nih.gov/123/"
        assert str(data["id"]) in slide.shapes[2].text
    with pytest.raises(ValidationError):
        Meeting(**{**data, "confirmed": False})
