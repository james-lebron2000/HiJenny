"""Tests for YAML due-diligence rule packs and the loader/applicability API (analysis-dev)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hijenny import config, rules
from hijenny.rules import RuleValidationError
from hijenny.schemas import FieldName, Owner, Priority

MINIMAL_PACK = """
schema_version: rule-pack-1.0
pack:
  pack_id: test-pack
  name: Test pack
  version: "1.2.3"
  therapeutic_area: oncology
  phase: [2]
rules:
  - rule_id: test.rule.one
    name: First test rule
    version: "1.0"
    applies_to: {}
    dimension: efficacy
    required_evidence:
      - id: req_one
        label: ORR value
        fields: [orr]
    question_template: "Please provide the ORR value supporting {requirement}."
    priority: high
    owner: Clinical Development
    required_documents: [CSR, SAP]
"""


def _write_pack(directory: Path, name: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Shipped packs
# ---------------------------------------------------------------------------


def test_shipped_packs_load_and_validate() -> None:
    loaded = rules.load_rules()
    assert len(loaded) >= 30, "expected the shipped DD checklist packs to load"

    expected_ids = {
        "onco1.study.design",
        "onco2.efficacy.orr",
        "onco2.population.definitions",
        "onco2.data.protocol_deviations",
        "onco3.efficacy.os",
        "endpoint.orr.value",
        "endpoint.pfs.value",
        "endpoint.os.value",
        "safety.ae.sae",
        "safety.dlt.grade3",
        "statistics.multiplicity",
        "statistics.missing_data",
        "quality.protocol_deviations",
        "quality.audit",
    }
    rule_ids = {rule.rule_id for rule in loaded}
    assert expected_ids <= rule_ids
    assert len(rule_ids) == len(loaded), "rule ids must be unique across packs"


def test_shipped_pack_files_exist() -> None:
    root = Path(config.RULES_DIR)
    expected = {
        "oncology/phase_1.yaml",
        "oncology/phase_2.yaml",
        "oncology/phase_3.yaml",
        "endpoints/orr.yaml",
        "endpoints/pfs.yaml",
        "endpoints/os.yaml",
        "safety/adverse_events.yaml",
        "safety/dose_limiting_toxicity.yaml",
        "statistics/statistical_quality.yaml",
        "data_quality/disposition_protocol_deviations.yaml",
    }
    missing = {name for name in expected if not (root / name).is_file()}
    assert not missing, f"missing shipped rule packs: {sorted(missing)}"


def test_shipped_packs_are_valid_yaml_and_use_known_fieldnames() -> None:
    field_values = {field.value for field in FieldName}
    owner_values = {owner.value for owner in Owner}
    priority_values = {priority.value for priority in Priority}
    paths = sorted(Path(config.RULES_DIR).rglob("*.yaml"))
    assert paths, "no YAML rule packs found"
    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(raw, dict) and isinstance(raw.get("rules"), list)
        for entry in raw["rules"]:
            assert entry["version"], f"{path}: rule without version"
            assert entry["priority"] in priority_values
            assert entry["owner"] in owner_values
            assert entry["question_template"].strip()
            for requirement in entry["required_evidence"]:
                assert requirement["fields"], f"{path}: requirement without fields"
                for field in requirement["fields"]:
                    assert field in field_values, f"{path}: unknown FieldName {field!r}"
            for check_id in entry.get("checks", []):
                assert check_id in rules.CHECKS


def test_expected_rule_priorities_and_owners() -> None:
    by_id = rules.rule_index(rules.load_rules())
    assert by_id["onco2.efficacy.orr"].priority == str(Priority.CRITICAL)
    assert by_id["onco2.efficacy.orr"].owner == str(Owner.CLINICAL)
    assert by_id["onco2.safety.sae"].owner == str(Owner.SAFETY)
    assert by_id["statistics.multiplicity"].owner == str(Owner.BIOSTATISTICS)
    assert by_id["quality.audit"].owner == str(Owner.REGULATORY)
    assert by_id["onco2.population.definitions"].dimension == "population"
    assert by_id["quality.audit"].dimension == "quality"
    assert by_id["onco2.data.subgroup"].dimension == "efficacy"


# ---------------------------------------------------------------------------
# Loader normalisation / inheritance
# ---------------------------------------------------------------------------


def test_pack_defaults_are_inherited_and_can_be_overridden(tmp_path: Path) -> None:
    _write_pack(tmp_path, "pack.yaml", MINIMAL_PACK)
    loaded = rules.load_rules(tmp_path)
    assert len(loaded) == 1
    rule = loaded[0]
    assert rule.pack_id == "test-pack"
    assert rule.pack_version == "1.2.3"
    assert rule.therapeutic_areas == ("oncology",)
    assert rule.phases == (2,)
    assert rule.required_documents == ("CSR", "SAP")
    assert rule.checks == rules.DEFAULT_CHECKS

    override = MINIMAL_PACK.replace("applies_to: {}", "applies_to: {phase: [1, 'Phase 3']}")
    _write_pack(tmp_path, "pack.yaml", override)
    overridden = rules.load_rules(tmp_path)[0]
    assert overridden.phases == (1, 3)


def test_select_rules_filters_by_phase_area_and_modality() -> None:
    loaded = rules.load_rules()
    oncology_phase2 = rules.select_rules(
        loaded,
        {
            "therapeutic_area": "oncology",
            "phase": "Phase 2",
            "modality": "oral small molecule",
        },
    )
    ids = {rule.rule_id for rule in oncology_phase2}
    assert "onco2.efficacy.orr" in ids
    assert "onco1.study.design" not in ids
    assert "onco3.efficacy.os" not in ids
    assert "endpoint.orr.value" in ids  # cross-cutting pack applies to phase 2

    assert rules.applicability(
        rules.rule_index(loaded)["onco1.study.design"],
        {"therapeutic_area": "oncology", "phase": "Phase 2"},
    ) == "not_applicable"

    # Unknown context dimensions are treated conservatively (included, uncertain).
    phase1_rule = rules.rule_index(loaded)["onco1.study.design"]
    assert rules.applicability(phase1_rule, {"therapeutic_area": "oncology"}) == "uncertain"
    assert rules.applicability(phase1_rule, {}) == "uncertain"
    selected_unknown = rules.select_rules(loaded, {})
    assert phase1_rule.rule_id in {rule.rule_id for rule in selected_unknown}

    # Non-oncology context: oncology phase packs drop out, cross-cutting packs stay.
    non_oncology = rules.select_rules(loaded, {"therapeutic_area": "immunology", "phase": 2})
    non_oncology_ids = {rule.rule_id for rule in non_oncology}
    assert "onco2.efficacy.orr" not in non_oncology_ids
    assert "safety.ae.sae" in non_oncology_ids


def test_infer_therapeutic_area() -> None:
    assert rules.infer_therapeutic_area("MTAP-deleted advanced solid tumors") == "oncology"
    assert rules.infer_therapeutic_area("moderate to severe plaque psoriasis") == "immunology"
    assert rules.infer_therapeutic_area("type 2 diabetes mellitus") == "metabolic"
    assert rules.infer_therapeutic_area("") is None
    assert rules.infer_therapeutic_area("unclassified syndrome xyz") is None


def test_format_question_template_keeps_unknown_placeholders() -> None:
    rendered = rules.format_question_template(
        "Please provide {requirement} in {documents}; see {unknown_placeholder}.",
        {"requirement": "ORR", "documents": ["CSR", "SAP"]},
    )
    assert "ORR" in rendered and "CSR, SAP" in rendered
    assert "{unknown_placeholder}" in rendered


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_content",
    [
        MINIMAL_PACK.replace("fields: [orr]", "fields: [not_a_field]"),
        MINIMAL_PACK.replace("    applies_to: {}\n", ""),
        MINIMAL_PACK.replace("priority: high", "priority: urgent"),
        MINIMAL_PACK.replace("owner: Clinical Development", "owner: Chief Vibes Officer"),
        MINIMAL_PACK.replace(
            "    required_documents: [CSR, SAP]",
            "    required_documents: [CSR, SAP]\n    checks: [magic_check]",
        ),
        MINIMAL_PACK.replace("rule-pack-1.0", "rule-pack-9.9"),
        MINIMAL_PACK.replace("    dimension: efficacy", "    dimension: efficacy\n    unexpected_key: true"),
        MINIMAL_PACK.replace("    required_evidence:", "    required_evidence: []\n    unused_required_evidence:"),
        MINIMAL_PACK.replace("    version: \"1.0\"", "    version: \"\""),
        MINIMAL_PACK.replace("  - id: req_one", "  - label: Missing id"),
    ],
)
def test_validation_rejects_malformed_rules(tmp_path: Path, bad_content: str) -> None:
    _write_pack(tmp_path, "bad.yaml", bad_content)
    with pytest.raises(RuleValidationError):
        rules.load_rules(tmp_path)


def test_validation_rejects_duplicate_rule_ids(tmp_path: Path) -> None:
    _write_pack(tmp_path, "a.yaml", MINIMAL_PACK)
    _write_pack(tmp_path, "b.yaml", MINIMAL_PACK.replace("test-pack", "other-pack"))
    with pytest.raises(RuleValidationError, match="duplicate rule_id"):
        rules.load_rules(tmp_path)


def test_validation_rejects_invalid_yaml(tmp_path: Path) -> None:
    _write_pack(tmp_path, "broken.yaml", "rules: [this is: not: valid")
    with pytest.raises(RuleValidationError, match="invalid YAML"):
        rules.load_rules(tmp_path)


def test_empty_directory_loads_nothing(tmp_path: Path) -> None:
    assert rules.load_rules(tmp_path / "does-not-exist") == []
