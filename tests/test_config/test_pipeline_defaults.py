"""Release defaults for the lossless schema-v13 ingestion profile."""

from sciforge.config.settings import AppConfig


def test_lossless_deterministic_pipeline_features_are_enabled_by_default() -> None:
    config = AppConfig()

    assert config.format_profile_enabled is True
    assert config.marker_ledger_enabled is True
    assert config.extract_active_carry_enabled is True
    assert config.provisional_id_remap_enabled is True
    assert config.patch_local_extraction_enabled is True
    assert config.extract_harness_enabled is True
    assert config.concept_graph_enabled is True
    assert config.item_evaluation_enabled is True
    assert config.segment_halo_lines > 0


def test_expensive_or_semantically_risky_features_remain_opt_in() -> None:
    config = AppConfig()

    assert config.extract_validator_enabled is False
    assert config.intra_doc_parallel_extract is False
    assert config.reconciliation_autofix is False
    assert config.extract_char_span_enabled is False
