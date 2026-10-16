"""Automated end-to-end scenario test (SPEC Section 7) on real FalkorDB."""

import pytest
from fastapi.testclient import TestClient
from recallguard.main import app
from recallguard.database import db

client = TestClient(app)

def test_full_spec_section_7_scenario():
    print("\n" + "=" * 75)
    print("STARTING SPEC SECTION 7 END-TO-END SCENARIO TEST ON REAL FALKORDB")
    print("=" * 75)

    # Clean graph before starting
    g = db.get_graph()
    try:
        g.delete()
    except Exception:
        pass
    db.init_schema()

    # -------------------------------------------------------------------------
    # Step 1: Session 1 (agent A)
    # config_repo (authoritative) asserts application.deployment_target = staging (c1)
    # -------------------------------------------------------------------------
    print("\n[Step 1] Ingesting c1: config_repo asserts application.deployment_target = staging")
    s1_resp = client.post("/claims", json={
        "claim_id": "c1",
        "subject": "application",
        "predicate": "deployment_target",
        "object": "staging",
        "source": {
            "id": "config_repo",
            "name": "Config Repository",
            "source_type": "config_file",
            "trust_tier": "authoritative"
        },
        "session_id": "session_1"
    })
    assert s1_resp.status_code == 200
    c1_data = s1_resp.json()
    assert c1_data["claim"]["id"] == "c1"
    assert c1_data["claim"]["status"] == "active"
    print(f"--> c1 created with status: {c1_data['claim']['status']}, edges: {c1_data['edges_created']}")

    # -------------------------------------------------------------------------
    # Step 2: Agent A summary
    # claim c2 "release_plan.environment = staging", DERIVED_FROM c1, source agent_a (verified)
    # An earlier 'deploy staging' verification is ALLOWed, relying on c1 and c2.
    # -------------------------------------------------------------------------
    print("\n[Step 2] Ingesting c2 (derived from c1) and running initial verification")
    s2_resp = client.post("/claims", json={
        "claim_id": "c2",
        "subject": "release_plan",
        "predicate": "environment",
        "object": "staging",
        "source": {
            "id": "agent_a",
            "name": "Agent A",
            "source_type": "agent",
            "trust_tier": "verified"
        },
        "session_id": "session_1",
        "derived_from": ["c1"]
    })
    assert s2_resp.status_code == 200
    c2_data = s2_resp.json()
    assert c2_data["claim"]["id"] == "c2"
    assert "DERIVED_FROM(c1)" in c2_data["edges_created"]
    print(f"--> c2 created, DERIVED_FROM c1 confirmed")

    # Verification: deploy staging
    v1_resp = client.post("/verify", json={
        "action_type": "deploy",
        "subject": "application",
        "predicate": "deployment_target",
        "requested_value": "staging",
        "session_id": "session_1"
    })
    assert v1_resp.status_code == 200
    v1_data = v1_resp.json()
    assert v1_data["decision"] == "ALLOW"
    assert v1_data["reason_code"] == "MATCHES_CURRENT_EVIDENCE"
    print(f"--> Initial verification for 'staging': DECISION={v1_data['decision']} ({v1_data['reason_code']})")

    # -------------------------------------------------------------------------
    # Step 3: Session 2
    # config_repo asserts deployment_target = production (c3) with supersedes_claim_id = c1
    # Response shows the blast radius: c2 plus the earlier ALLOW decision.
    # -------------------------------------------------------------------------
    print("\n[Step 3] Ingesting c3 superseding c1 -> checking blast radius")
    s3_resp = client.post("/claims", json={
        "claim_id": "c3",
        "subject": "application",
        "predicate": "deployment_target",
        "object": "production",
        "source": {
            "id": "config_repo",
            "name": "Config Repository",
            "source_type": "config_file",
            "trust_tier": "authoritative"
        },
        "session_id": "session_2",
        "supersedes_claim_id": "c1"
    })
    assert s3_resp.status_code == 200
    c3_data = s3_resp.json()
    assert c3_data["superseded_claim_id"] == "c1"
    assert c3_data["blast_radius"] is not None

    br = c3_data["blast_radius"]
    print(f"--> Blast radius summary: {br['summary']}")
    assert len(br["downstream_claims"]) == 1
    assert br["downstream_claims"][0]["id"] == "c2"
    assert br["downstream_claims"][0]["depth"] == 1
    assert len(br["earlier_decisions"]) == 1
    assert br["earlier_decisions"][0]["id"] == v1_data["decision_id"]
    assert br["earlier_decisions"][0]["needs_review"] is True
    print(f"--> Blast radius accurately captured: derived claim c2 and earlier ALLOW decision {v1_data['decision_id']}")

    # -------------------------------------------------------------------------
    # Step 4: Session 3 (agent B)
    # tries 'deploy staging', so BLOCK REQUESTED_VALUE_SUPERSEDED, evidence c1, c3, edge SUPERSEDES
    # -------------------------------------------------------------------------
    print("\n[Step 4] Session 3 (Agent B): tries 'deploy staging'")
    v2_resp = client.post("/verify", json={
        "action_type": "deploy",
        "subject": "application",
        "predicate": "deployment_target",
        "requested_value": "staging",
        "session_id": "session_3"
    })
    assert v2_resp.status_code == 200
    v2_data = v2_resp.json()
    print(f"--> Verification result: DECISION={v2_data['decision']} ({v2_data['reason_code']})")
    assert v2_data["decision"] == "BLOCK"
    assert v2_data["reason_code"] == "REQUESTED_VALUE_SUPERSEDED"
    ev_ids = {e["claim_id"] for e in v2_data["evidence"]}
    assert "c1" in ev_ids
    assert "c3" in ev_ids
    rel_types = {r["type"] for r in v2_data["relationships"]}
    assert "SUPERSEDES" in rel_types

    # -------------------------------------------------------------------------
    # Step 5: Session 3b
    # agent B tries to act on c2 (release_plan.environment = staging, derived from c1, tainted)
    # -> BLOCK STALE_DEPENDENCY
    # -------------------------------------------------------------------------
    print("\n[Step 5] Session 3b (Agent B): tries to act on release_plan.environment = 'staging'")
    v3_resp = client.post("/verify", json={
        "action_type": "release",
        "subject": "release_plan",
        "predicate": "environment",
        "requested_value": "staging",
        "session_id": "session_3b"
    })
    assert v3_resp.status_code == 200
    v3_data = v3_resp.json()
    print(f"--> Verification result: DECISION={v3_data['decision']} ({v3_data['reason_code']})")
    assert v3_data["decision"] == "BLOCK"
    assert v3_data["reason_code"] == "STALE_DEPENDENCY"
    assert v3_data["evidence"][0]["claim_id"] == "c2"
    assert v3_data["evidence"][0]["role"] == "tainted"

    # -------------------------------------------------------------------------
    # Step 6: Session 4
    # 'deploy production' gives ALLOW MATCHES_CURRENT_EVIDENCE
    # -------------------------------------------------------------------------
    print("\n[Step 6] Session 4: tries 'deploy production'")
    v4_resp = client.post("/verify", json={
        "action_type": "deploy",
        "subject": "application",
        "predicate": "deployment_target",
        "requested_value": "production",
        "session_id": "session_4"
    })
    assert v4_resp.status_code == 200
    v4_data = v4_resp.json()
    print(f"--> Verification result: DECISION={v4_data['decision']} ({v4_data['reason_code']})")
    assert v4_data["decision"] == "ALLOW"
    assert v4_data["reason_code"] == "MATCHES_CURRENT_EVIDENCE"
    assert v4_data["governing_value"] == "production"

    # -------------------------------------------------------------------------
    # Step 7: Bonus beat
    # An agent-tier source claims deployment_target = canary with a newer timestamp
    # and no supersession. It is recorded as CONTRADICTS (canary points to c3),
    # only canary becomes disputed, c3 stays active and keeps governing.
    # -------------------------------------------------------------------------
    print("\n[Step 7] Bonus Beat: Rogue agent claims deployment_target = 'canary' without supersession")
    canary_resp = client.post("/claims", json={
        "claim_id": "c_canary",
        "subject": "application",
        "predicate": "deployment_target",
        "object": "canary",
        "source": {
            "id": "agent_rogue",
            "name": "Rogue Agent",
            "source_type": "agent",
            "trust_tier": "agent"
        },
        "session_id": "session_bonus"
    })
    assert canary_resp.status_code == 200
    canary_data = canary_resp.json()
    assert canary_data["claim"]["id"] == "c_canary"
    assert canary_data["claim"]["status"] == "disputed"
    print(f"--> c_canary created with status: {canary_data['claim']['status']}")

    # c3 must still be active and untouched!
    c3_check = client.get("/claims/c3").json()
    assert c3_check["claim"]["status"] == "active"
    print(f"--> c3 status confirmed: {c3_check['claim']['status']} (UNTOUCHED by low-trust claim)")

    # Verify edge (c_canary)-[:CONTRADICTS]->(c3)
    edge_res = g.query("MATCH (a:Claim {id: 'c_canary'})-[:CONTRADICTS]->(b:Claim {id: 'c3'}) RETURN count(*)").result_set[0][0]
    assert edge_res == 1
    print("--> Edge (c_canary)-[:CONTRADICTS]->(c3) verified in graph!")

    # Verify that deployment_target = production STILL gives ALLOW
    v5_resp = client.post("/verify", json={
        "action_type": "deploy",
        "subject": "application",
        "predicate": "deployment_target",
        "requested_value": "production",
        "session_id": "session_bonus"
    })
    assert v5_resp.status_code == 200
    v5_data = v5_resp.json()
    assert v5_data["decision"] == "ALLOW"
    assert v5_data["reason_code"] == "MATCHES_CURRENT_EVIDENCE"
    print(f"--> Post-bonus verification for 'production': DECISION={v5_data['decision']} ({v5_data['reason_code']})")

    # And verify that attempting to deploy 'canary' is BLOCKED
    v6_resp = client.post("/verify", json={
        "action_type": "deploy",
        "subject": "application",
        "predicate": "deployment_target",
        "requested_value": "canary",
        "session_id": "session_bonus"
    })
    assert v6_resp.status_code == 200
    v6_data = v6_resp.json()
    assert v6_data["decision"] == "BLOCK"
    assert v6_data["reason_code"] == "REQUESTED_VALUE_CONFLICTS_CURRENT"
    print(f"--> Attempt to deploy 'canary': DECISION={v6_data['decision']} ({v6_data['reason_code']})")

    print("\n" + "=" * 75)
    print("SPEC SECTION 7 SCENARIO TEST COMPLETED SUCCESSFULLY!")
    print("=" * 75 + "\n")
