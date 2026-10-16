"""Unit tests for RecallGuard Policy 1.1 pure evaluation function."""

import pytest
from recallguard.models import DecisionType
from recallguard.policy import PolicyClaim, evaluate_policy

def test_rule_1_graph_failure():
    res = evaluate_policy("app", "target", "staging", claims=[], graph_error=True)
    assert res.decision == DecisionType.ABSTAIN
    assert res.reason_code == "GRAPH_UNAVAILABLE"

def test_rule_2_no_evidence():
    res = evaluate_policy("app", "target", "staging", claims=[], graph_error=False)
    assert res.decision == DecisionType.ABSTAIN
    assert res.reason_code == "NO_EVIDENCE"

def test_rule_3_stale_dependency():
    tainted = PolicyClaim(
        id="c2",
        subject="app",
        predicate="target",
        object="staging",
        status="active",
        source_id="agent1",
        source_name="Agent 1",
        trust_tier="verified",
        is_tainted=True
    )
    res = evaluate_policy("app", "target", "staging", claims=[tainted])
    assert res.decision == DecisionType.BLOCK
    assert res.reason_code == "STALE_DEPENDENCY"

def test_rule_4_requested_value_superseded():
    superseded = PolicyClaim(
        id="c1",
        subject="app",
        predicate="target",
        object="staging",
        status="superseded",
        source_id="cfg",
        source_name="Config Repo",
        trust_tier="authoritative",
        is_tainted=False,
        chain_head={"id": "c3", "object": "production", "status": "active", "trust_tier": "authoritative"}
    )
    res = evaluate_policy("app", "target", "staging", claims=[superseded])
    assert res.decision == DecisionType.BLOCK
    assert res.reason_code == "REQUESTED_VALUE_SUPERSEDED"

def test_rule_5_contested_claim_disputed_verified():
    # Disputed claim from verified source and no higher-ranked claim
    disputed = PolicyClaim(
        id="c_disp",
        subject="app",
        predicate="target",
        object="staging",
        status="disputed",
        source_id="sec_team",
        source_name="SecOps",
        trust_tier="verified"
    )
    res = evaluate_policy("app", "target", "staging", claims=[disputed])
    assert res.decision == DecisionType.ABSTAIN
    assert res.reason_code == "UNRESOLVED_CONFLICT"

def test_rule_6_insufficient_trust():
    agent_claim = PolicyClaim(
        id="c_agent",
        subject="app",
        predicate="target",
        object="staging",
        status="active",
        source_id="bot",
        source_name="Bot",
        trust_tier="agent"
    )
    res = evaluate_policy("app", "target", "staging", claims=[agent_claim])
    assert res.decision == DecisionType.ABSTAIN
    assert res.reason_code == "INSUFFICIENT_TRUST"

def test_rule_7_unresolved_conflict_equal_verified_tiers():
    c1 = PolicyClaim(
        id="c1",
        subject="app",
        predicate="target",
        object="val_a",
        status="active",
        source_id="src1",
        source_name="Src 1",
        trust_tier="verified"
    )
    c2 = PolicyClaim(
        id="c2",
        subject="app",
        predicate="target",
        object="val_b",
        status="active",
        source_id="src2",
        source_name="Src 2",
        trust_tier="verified"
    )
    res = evaluate_policy("app", "target", "val_a", claims=[c1, c2])
    assert res.decision == DecisionType.ABSTAIN
    assert res.reason_code == "UNRESOLVED_CONFLICT"

def test_rule_8_conflicts_current():
    auth = PolicyClaim(
        id="c3",
        subject="app",
        predicate="target",
        object="production",
        status="active",
        source_id="cfg",
        source_name="Config Repo",
        trust_tier="authoritative"
    )
    res = evaluate_policy("app", "target", "staging", claims=[auth])
    assert res.decision == DecisionType.BLOCK
    assert res.reason_code == "REQUESTED_VALUE_CONFLICTS_CURRENT"
    assert res.governing_value == "production"

def test_rule_9_matches_current():
    auth = PolicyClaim(
        id="c3",
        subject="app",
        predicate="target",
        object="production",
        status="active",
        source_id="cfg",
        source_name="Config Repo",
        trust_tier="authoritative"
    )
    res = evaluate_policy("app", "target", "production", claims=[auth])
    assert res.decision == DecisionType.ALLOW
    assert res.reason_code == "MATCHES_CURRENT_EVIDENCE"
    assert res.governing_value == "production"
