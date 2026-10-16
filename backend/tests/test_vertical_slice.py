"""Integration tests for RecallGuard vertical slice on real FalkorDB."""

import pytest
from fastapi.testclient import TestClient
from recallguard.main import app
from recallguard.database import db

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean_graph():
    """Wipes test graph before each test to ensure isolated execution."""
    g = db.get_graph()
    try:
        g.delete()
    except Exception:
        pass
    db.init_schema()
    yield

def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["falkordb_connected"] is True

def test_ingestion_idempotency():
    claim_payload = {
        "claim_id": "c_idemp_1",
        "subject": "service.cache",
        "predicate": "enabled",
        "object": "true",
        "source": {
            "id": "config_sys",
            "name": "Config System",
            "source_type": "config_file",
            "trust_tier": "authoritative"
        },
        "session_id": "sess_1"
    }

    # First ingest
    r1 = client.post("/claims", json=claim_payload)
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["claim"]["id"] == "c_idemp_1"

    # Second ingest of identical content
    r2 = client.post("/claims", json=claim_payload)
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["claim"]["id"] == "c_idemp_1"
    assert data2["claim"]["source_claim_key"] == data1["claim"]["source_claim_key"]

    # Verify only 1 node in FalkorDB
    g = db.get_graph()
    count_res = g.query("MATCH (c:Claim {subject: 'service.cache'}) RETURN count(c)").result_set[0][0]
    assert count_res == 1

def test_supersession_accept_and_blast_radius():
    # 1. Ingest base claim c1
    c1_payload = {
        "claim_id": "c1",
        "subject": "app",
        "predicate": "target",
        "object": "staging",
        "source": {
            "id": "repo",
            "name": "Repo",
            "source_type": "config_file",
            "trust_tier": "authoritative"
        },
        "session_id": "s1"
    }
    client.post("/claims", json=c1_payload)

    # 2. Ingest derived claim c2
    c2_payload = {
        "claim_id": "c2",
        "subject": "plan",
        "predicate": "env",
        "object": "staging",
        "source": {
            "id": "agent",
            "name": "Agent",
            "source_type": "agent",
            "trust_tier": "verified"
        },
        "session_id": "s1",
        "derived_from": ["c1"]
    }
    client.post("/claims", json=c2_payload)

    # 3. Simulate an earlier ALLOW decision relying on c1
    v_res = client.post("/verify", json={
        "action_type": "deploy",
        "subject": "app",
        "predicate": "target",
        "requested_value": "staging",
        "session_id": "s1"
    })
    assert v_res.json()["decision"] == "ALLOW"

    # 4. Ingest c3 superseding c1
    c3_payload = {
        "claim_id": "c3",
        "subject": "app",
        "predicate": "target",
        "object": "production",
        "source": {
            "id": "repo",
            "name": "Repo",
            "source_type": "config_file",
            "trust_tier": "authoritative"
        },
        "session_id": "s2",
        "supersedes_claim_id": "c1"
    }
    r3 = client.post("/claims", json=c3_payload)
    assert r3.status_code == 200
    data3 = r3.json()
    assert data3["superseded_claim_id"] == "c1"
    assert data3["blast_radius"] is not None
    assert len(data3["blast_radius"]["downstream_claims"]) == 1
    assert data3["blast_radius"]["downstream_claims"][0]["id"] == "c2"
    assert len(data3["blast_radius"]["earlier_decisions"]) == 1
    assert data3["blast_radius"]["earlier_decisions"][0]["needs_review"] is True

def test_supersession_rejection_and_contradiction():
    # 1. Authoritative base claim
    client.post("/claims", json={
        "claim_id": "auth_1",
        "subject": "cluster",
        "predicate": "region",
        "object": "us-east-1",
        "source": {
            "id": "admin",
            "name": "Admin",
            "source_type": "human",
            "trust_tier": "authoritative"
        },
        "session_id": "s1"
    })

    # 2. Agent tries to supersede authoritative claim -> Rejected!
    agent_sup = client.post("/claims", json={
        "claim_id": "agent_try",
        "subject": "cluster",
        "predicate": "region",
        "object": "eu-central-1",
        "source": {
            "id": "bot",
            "name": "Bot",
            "source_type": "agent",
            "trust_tier": "agent"  # rank 1 < rank 3
        },
        "session_id": "s2",
        "supersedes_claim_id": "auth_1"
    })
    assert agent_sup.status_code == 200
    data = agent_sup.json()
    assert data["superseded_claim_id"] is None
    assert data["supersession_rejected"] is not None
    assert "below" in data["supersession_rejected"]

    # auth_1 remains active, agent_try becomes disputed
    auth_check = client.get("/claims/auth_1").json()
    assert auth_check["claim"]["status"] == "active"

    bot_check = client.get("/claims/agent_try").json()
    assert bot_check["claim"]["status"] == "disputed"

def test_contradiction_tiers_protection():
    # Authoritative claim
    client.post("/claims", json={
        "claim_id": "c_auth",
        "subject": "db",
        "predicate": "mode",
        "object": "primary",
        "source": {
            "id": "cfg",
            "name": "Config",
            "source_type": "config_file",
            "trust_tier": "authoritative"
        },
        "session_id": "s1"
    })

    # Low-trust agent claims contradictory value without supersedes_claim_id
    client.post("/claims", json={
        "claim_id": "c_agent",
        "subject": "db",
        "predicate": "mode",
        "object": "replica",
        "source": {
            "id": "bot",
            "name": "Bot",
            "source_type": "agent",
            "trust_tier": "agent"
        },
        "session_id": "s2"
    })

    # Assert c_auth is still active and c_agent is disputed
    c_auth_resp = client.get("/claims/c_auth").json()
    assert c_auth_resp["claim"]["status"] == "active"

    c_agent_resp = client.get("/claims/c_agent").json()
    assert c_agent_resp["claim"]["status"] == "disputed"

    # Verify edge (c_agent)-[:CONTRADICTS]->(c_auth)
    g = db.get_graph()
    edge_chk = g.query("MATCH (a:Claim {id: 'c_agent'})-[:CONTRADICTS]->(b:Claim {id: 'c_auth'}) RETURN count(*)").result_set[0][0]
    assert edge_chk == 1
