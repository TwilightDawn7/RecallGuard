"""Synthetic scenario seeder for SPEC Section 7."""

from fastapi import HTTPException
from recallguard.database import db
from recallguard.models import ClaimCreateRequest, SourceInput, SourceType, TrustTier, VerifyRequest
from recallguard.main import ingest_claim, verify_endpoint

def seed_demo_scenario():
    """
    Seeds initial steps 1 and 2 of the synthetic demo scenario:
    1. config_repo asserts application.deployment_target = staging (c1)
    2. agent_a asserts release_plan.environment = staging derived from c1 (c2)
    3. Earlier deploy staging verification recorded as ALLOW relying on c1 and c2
    """
    # 1. c1
    c1_req = ClaimCreateRequest(
        claim_id="c1",
        subject="application",
        predicate="deployment_target",
        object="staging",
        source=SourceInput(
            id="config_repo",
            name="Config Repository",
            source_type=SourceType.CONFIG_FILE,
            trust_tier=TrustTier.AUTHORITATIVE
        ),
        session_id="session_1"
    )
    c1_res = ingest_claim(c1_req)

    # 2. c2 derived from c1
    c2_req = ClaimCreateRequest(
        claim_id="c2",
        subject="release_plan",
        predicate="environment",
        object="staging",
        source=SourceInput(
            id="agent_a",
            name="Agent A",
            source_type=SourceType.AGENT,
            trust_tier=TrustTier.VERIFIED
        ),
        session_id="session_1",
        derived_from=["c1"]
    )
    c2_res = ingest_claim(c2_req)

    # 3. Earlier deploy staging verification
    v_req = VerifyRequest(
        action_type="deploy",
        subject="application",
        predicate="deployment_target",
        requested_value="staging",
        session_id="session_1"
    )
    v_res = verify_endpoint(v_req)

    return {
        "status": "seeded",
        "claims": [c1_res.claim.id, c2_res.claim.id],
        "initial_decision": v_res.decision
    }
