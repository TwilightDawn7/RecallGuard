"""Unit tests for normalization and idempotency key computation."""

from recallguard.normalization import normalize_text, normalize_object, compute_source_claim_key

def test_normalization():
    assert normalize_text("  Application.Deployment  Target  ") == "application.deployment_target"
    assert normalize_text("release_plan.ENVIRONMENT") == "release_plan.environment"
    assert normalize_object("  staging  \n") == "staging"

def test_idempotency_key_stability():
    k1 = compute_source_claim_key("cfg_1", "App.Target", "env", "staging")
    k2 = compute_source_claim_key("cfg_1", "app.target", "env", "  staging  ")
    assert k1 == k2
    assert len(k1) == 64
