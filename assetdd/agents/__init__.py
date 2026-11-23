"""Analysis agents: consistency, evidence gap and seller-question generation.

Each module exposes the pipeline stage-handler contract::

    run(session, project_id: str, force: bool = False) -> dict  # stats

The agents are deterministic, evidence-linked and idempotent (stable dedup keys),
and they never invent values: every finding points at persisted evidence rows
with document/page provenance.
"""
