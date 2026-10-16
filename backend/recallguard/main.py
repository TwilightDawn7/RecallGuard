"""RecallGuard FastAPI backend application."""

import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

from recallguard.database import db
from recallguard import queries
from recallguard.models import (
    ClaimCreateRequest, ClaimResponse, ClaimIngestResponse,
    BlastRadiusResponse, BlastRadiusClaim, BlastRadiusDecision,
    VerifyRequest, VerifyResponse, EvidenceItem, RelationshipItem,
    DecisionRecord, StatsResponse, ClaimStatus, TrustTier, DecisionType,
    EvidenceRole, SourceInput
)
from recallguard.normalization import normalize_text, normalize_object, compute_source_claim_key
from recallguard.policy import PolicyClaim, evaluate_policy

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize graph schema, indexes, and constraints on startup
    try:
        db.init_schema()
    except Exception as e:
        print(f"Schema initialization notice: {e}")
    yield

app = FastAPI(
    title="RecallGuard API",
    description="Graph-native memory verification and action gate for AI agents",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_blast_radius(graph, claim_id: str) -> BlastRadiusResponse:
    """Computes blast radius for a superseded claim."""
    downstream_claims: List[BlastRadiusClaim] = []
    earlier_decisions: List[BlastRadiusDecision] = []

    res_downstream = graph.query(queries.Q_BLAST_RADIUS_DOWNSTREAM, {"id": claim_id})
    downstream_ids = []
    for row in res_downstream.result_set:
        c_id, subj, pred, obj, st, depth = row
        downstream_claims.append(
            BlastRadiusClaim(
                id=c_id,
                subject=subj,
                predicate=pred,
                object=obj,
                status=st,
                depth=int(depth)
            )
        )
        downstream_ids.append(c_id)

    res_decisions = graph.query(
        queries.Q_BLAST_RADIUS_DECISIONS,
        {"id": claim_id, "downstream_ids": downstream_ids}
    )
    for row in res_decisions.result_set:
        d_id, act, subj, pred, req_val, dec, created = row
        earlier_decisions.append(
            BlastRadiusDecision(
                id=d_id,
                action_type=act,
                subject=subj,
                predicate=pred,
                requested_value=req_val,
                decision=dec,
                created_at=created,
                needs_review=True
            )
        )

    summary = (
        f"This update invalidates {len(downstream_claims)} derived claims "
        f"and {len(earlier_decisions)} earlier ALLOW decisions"
    )

    return BlastRadiusResponse(
        target_claim_id=claim_id,
        downstream_claims=downstream_claims,
        earlier_decisions=earlier_decisions,
        summary=summary
    )

@app.get("/health")
def health():
    connected = db.ping()
    return {
        "status": "ok",
        "falkordb_connected": connected,
        "version": "0.1.0"
    }

@app.post("/claims", response_model=ClaimIngestResponse)
def ingest_claim(req: ClaimCreateRequest):
    graph = db.get_graph()

    # Step 1: Validate and normalize
    norm_subject = normalize_text(req.subject)
    norm_predicate = normalize_text(req.predicate)
    norm_object = normalize_object(req.object)

    if not norm_subject or not norm_predicate:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Subject and predicate cannot be empty after normalization"
        )

    # Validate existence of derived_from claims
    if req.derived_from:
        for parent_id in req.derived_from:
            chk = graph.query("MATCH (c:Claim {id: $id}) RETURN c.id", {"id": parent_id})
            if not chk.result_set:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Derived-from claim '{parent_id}' does not exist"
                )

    # Validate existence of superseded claim if given
    old_claim_row = None
    if req.supersedes_claim_id:
        chk = graph.query(queries.Q_GET_CLAIM_BY_ID, {"id": req.supersedes_claim_id})
        if not chk.result_set:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Supersedes target claim '{req.supersedes_claim_id}' does not exist"
            )
        old_claim_row = chk.result_set[0]

    # Step 2: Compute source_claim_key
    claim_id = req.claim_id or f"c_{uuid.uuid4().hex[:8]}"
    claim_key = compute_source_claim_key(req.source.id, norm_subject, norm_predicate, norm_object)
    now_iso = datetime.now(timezone.utc).isoformat()
    demo_flag = (os.getenv("RECALLGUARD_ENV") == "demo")

    # Step 3: MERGE Claim, Source, Session
    ingest_params = {
        "id": claim_id,
        "subject": norm_subject,
        "predicate": norm_predicate,
        "object": norm_object,
        "status": ClaimStatus.ACTIVE.value,
        "created_at": now_iso,
        "source_claim_key": claim_key,
        "source_id": req.source.id,
        "source_name": req.source.name,
        "source_type": req.source.source_type.value,
        "trust_tier": req.source.trust_tier.value,
        "session_id": req.session_id,
        "agent_name": req.source.name,
        "demo": demo_flag
    }
    ingest_res = graph.query(queries.Q_INGEST_CLAIM, ingest_params)
    actual_claim_id = ingest_res.result_set[0][0]

    edges_created = ["ASSERTED", "PRODUCED"]

    # Step 4: Add DERIVED_FROM edges
    if req.derived_from:
        for parent_id in req.derived_from:
            graph.query(queries.Q_CREATE_DERIVED_FROM, {"child_id": actual_claim_id, "parent_id": parent_id})
            edges_created.append(f"DERIVED_FROM({parent_id})")

    superseded_id = None
    supersession_rejected = None
    contradictions_created = []
    blast_radius = None

    # Step 5: Supersession Authorization
    if req.supersedes_claim_id and old_claim_row:
        old_id, old_subj, old_pred, old_obj, old_st, old_created, old_key, old_src_id, old_src_name, old_src_type, old_src_tier, old_sess = old_claim_row
        
        old_tier_rank = TrustTier(old_src_tier).rank if old_src_tier else 0
        new_tier_rank = req.source.trust_tier.rank

        same_subj_pred = (norm_subject == old_subj and norm_predicate == old_pred)
        new_is_verified = (new_tier_rank >= TrustTier.VERIFIED.rank)
        tier_sufficient = (new_tier_rank >= old_tier_rank)

        if same_subj_pred and new_is_verified and tier_sufficient:
            # Accepted supersession!
            graph.query(
                queries.Q_CREATE_SUPERSEDES,
                {
                    "new_id": actual_claim_id,
                    "old_id": req.supersedes_claim_id,
                    "at": now_iso,
                    "by_source": req.source.id
                }
            )
            edges_created.append("SUPERSEDES")
            superseded_id = req.supersedes_claim_id
            blast_radius = get_blast_radius(graph, req.supersedes_claim_id)
        else:
            # Rejected supersession
            reasons = []
            if not same_subj_pred:
                reasons.append("Subject/predicate mismatch with target claim")
            if not new_is_verified:
                reasons.append("Source trust tier is below 'verified'")
            if not tier_sufficient:
                reasons.append(f"Source trust tier ({req.source.trust_tier.value}) is below target source tier ({old_src_tier})")
            supersession_rejected = "; ".join(reasons)

            # Apply contradiction rule between new claim and rejected target claim
            if new_tier_rank < old_tier_rank:
                graph.query(queries.Q_CREATE_CONTRADICTS, {"from_id": actual_claim_id, "to_id": req.supersedes_claim_id})
                graph.query(queries.Q_SET_CLAIM_STATUS, {"id": actual_claim_id, "status": ClaimStatus.DISPUTED.value})
            elif new_tier_rank > old_tier_rank:
                graph.query(queries.Q_CREATE_CONTRADICTS, {"from_id": req.supersedes_claim_id, "to_id": actual_claim_id})
                graph.query(queries.Q_SET_CLAIM_STATUS, {"id": req.supersedes_claim_id, "status": ClaimStatus.DISPUTED.value})
            else:
                graph.query(queries.Q_CREATE_CONTRADICTS, {"from_id": actual_claim_id, "to_id": req.supersedes_claim_id})
                graph.query(queries.Q_SET_CLAIM_STATUS, {"id": actual_claim_id, "status": ClaimStatus.DISPUTED.value})
                graph.query(queries.Q_SET_CLAIM_STATUS, {"id": req.supersedes_claim_id, "status": ClaimStatus.DISPUTED.value})
            contradictions_created.append(req.supersedes_claim_id)

    # Step 6: Contradiction rule for active conflicting claims with same (s, p) and different object
    if not superseded_id:
        existing = graph.query(
            queries.Q_GET_CLAIMS_FOR_SUBJECT_PREDICATE,
            {"subject": norm_subject, "predicate": norm_predicate}
        )
        for row in existing.result_set:
            ex_id, ex_s, ex_p, ex_obj, ex_status, ex_created, ex_key, ex_src_id, ex_src_name, ex_src_type, ex_src_tier = row
            if ex_id == actual_claim_id:
                continue
            if ex_status != ClaimStatus.SUPERSEDED.value and ex_obj != norm_object:
                ex_rank = TrustTier(ex_src_tier).rank if ex_src_tier else 0
                new_rank = req.source.trust_tier.rank

                if new_rank < ex_rank:
                    # Lower tier contradicts higher tier: only lower tier is disputed
                    graph.query(queries.Q_CREATE_CONTRADICTS, {"from_id": actual_claim_id, "to_id": ex_id})
                    graph.query(queries.Q_SET_CLAIM_STATUS, {"id": actual_claim_id, "status": ClaimStatus.DISPUTED.value})
                elif new_rank > ex_rank:
                    # Existing lower tier is marked disputed
                    graph.query(queries.Q_CREATE_CONTRADICTS, {"from_id": ex_id, "to_id": actual_claim_id})
                    graph.query(queries.Q_SET_CLAIM_STATUS, {"id": ex_id, "status": ClaimStatus.DISPUTED.value})
                else:
                    # Tiers equal: mark both disputed
                    graph.query(queries.Q_CREATE_CONTRADICTS, {"from_id": actual_claim_id, "to_id": ex_id})
                    graph.query(queries.Q_SET_CLAIM_STATUS, {"id": actual_claim_id, "status": ClaimStatus.DISPUTED.value})
                    graph.query(queries.Q_SET_CLAIM_STATUS, {"id": ex_id, "status": ClaimStatus.DISPUTED.value})
                
                contradictions_created.append(ex_id)

    # Fetch updated claim state
    updated_chk = graph.query(queries.Q_GET_CLAIM_BY_ID, {"id": actual_claim_id}).result_set[0]
    claim_resp = ClaimResponse(
        id=updated_chk[0],
        subject=updated_chk[1],
        predicate=updated_chk[2],
        object=updated_chk[3],
        status=ClaimStatus(updated_chk[4]),
        created_at=updated_chk[5],
        source_claim_key=updated_chk[6],
        source=SourceInput(
            id=updated_chk[7],
            name=updated_chk[8] or updated_chk[7],
            source_type=updated_chk[9] or "agent",
            trust_tier=updated_chk[10] or "unknown"
        ),
        session_id=updated_chk[11] or req.session_id
    )

    return ClaimIngestResponse(
        claim=claim_resp,
        edges_created=edges_created,
        superseded_claim_id=superseded_id,
        supersession_rejected=supersession_rejected,
        contradictions_created=contradictions_created,
        blast_radius=blast_radius
    )

@app.get("/claims", response_model=List[ClaimResponse])
def list_claims(subject: Optional[str] = None, status: Optional[str] = None):
    graph = db.get_graph()
    res = graph.query(queries.Q_LIST_CLAIMS)
    claims: List[ClaimResponse] = []
    norm_sub = normalize_text(subject) if subject else None

    for row in res.result_set:
        c_id, s, p, o, st, created, key, src_id, src_name, src_type, src_tier, sess_id = row
        if norm_sub and s != norm_sub:
            continue
        if status and st != status:
            continue
        claims.append(
            ClaimResponse(
                id=c_id,
                subject=s,
                predicate=p,
                object=o,
                status=ClaimStatus(st),
                created_at=created,
                source_claim_key=key,
                source=SourceInput(
                    id=src_id or "unknown",
                    name=src_name or "unknown",
                    source_type=src_type or "agent",
                    trust_tier=src_tier or "unknown"
                ),
                session_id=sess_id or "default"
            )
        )
    return claims

@app.get("/claims/{id}")
def get_claim(id: str):
    graph = db.get_graph()
    res = graph.query(queries.Q_GET_CLAIM_BY_ID, {"id": id})
    if not res.result_set:
        raise HTTPException(status_code=404, detail=f"Claim '{id}' not found")

    row = res.result_set[0]
    c_id, s, p, o, st, created, key, src_id, src_name, src_type, src_tier, sess_id = row

    # Incident relationships
    rels_res = graph.query(queries.Q_GET_CLAIM_RELATIONSHIPS, {"id": id})
    relationships = [
        {"type": r[0], "from_id": r[1], "to_id": r[2]}
        for r in rels_res.result_set
    ]

    return {
        "claim": ClaimResponse(
            id=c_id,
            subject=s,
            predicate=p,
            object=o,
            status=ClaimStatus(st),
            created_at=created,
            source_claim_key=key,
            source=SourceInput(
                id=src_id or "unknown",
                name=src_name or "unknown",
                source_type=src_type or "agent",
                trust_tier=src_tier or "unknown"
            ),
            session_id=sess_id or "default"
        ),
        "relationships": relationships
    }

@app.get("/claims/{id}/blast-radius", response_model=BlastRadiusResponse)
def blast_radius_endpoint(id: str):
    graph = db.get_graph()
    chk = graph.query("MATCH (c:Claim {id: $id}) RETURN c.id", {"id": id})
    if not chk.result_set:
        raise HTTPException(status_code=404, detail=f"Claim '{id}' not found")
    return get_blast_radius(graph, id)

@app.post("/verify", response_model=VerifyResponse)
def verify_endpoint(req: VerifyRequest):
    norm_subject = normalize_text(req.subject)
    norm_predicate = normalize_text(req.predicate)
    norm_value = normalize_object(req.requested_value)

    claims_data: List[PolicyClaim] = []
    graph_error = False

    try:
        graph = db.get_graph()
        rows = graph.query(
            queries.Q_GET_CLAIMS_FOR_SUBJECT_PREDICATE,
            {"subject": norm_subject, "predicate": norm_predicate}
        ).result_set

        for row in rows:
            c_id, s, p, o, st, created, key, src_id, src_name, src_type, src_tier = row
            
            # Check taint via DERIVED_FROM*1..5 path
            taint_rows = graph.query(queries.Q_CHECK_CLAIM_TAINT, {"id": c_id}).result_set
            is_tainted = (len(taint_rows) > 0)

            # If superseded, find chain head via SUPERSEDES*1..5
            chain_head = None
            if st == ClaimStatus.SUPERSEDED.value:
                head_rows = graph.query(queries.Q_FIND_CHAIN_HEAD, {"old_id": c_id}).result_set
                if head_rows:
                    h_id, h_subj, h_pred, h_obj, h_st, h_tier = head_rows[0]
                    chain_head = {
                        "id": h_id,
                        "subject": h_subj,
                        "predicate": h_pred,
                        "object": h_obj,
                        "status": h_st,
                        "trust_tier": h_tier
                    }

            claims_data.append(
                PolicyClaim(
                    id=c_id,
                    subject=s,
                    predicate=p,
                    object=o,
                    status=st,
                    source_id=src_id or "unknown",
                    source_name=src_name or "unknown",
                    trust_tier=src_tier or "unknown",
                    is_tainted=is_tainted,
                    chain_head=chain_head
                )
            )
    except Exception as e:
        print(f"Graph query error during verification: {e}")
        graph_error = True

    # Deterministic pure policy evaluation
    result = evaluate_policy(
        subject=norm_subject,
        predicate=norm_predicate,
        requested_value=norm_value,
        claims=claims_data,
        graph_error=graph_error
    )

    decision_id = f"dec_{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    demo_flag = (os.getenv("RECALLGUARD_ENV") == "demo")

    # Record Decision node and RELIED_ON edges if graph is available
    relationships: List[RelationshipItem] = []
    if not graph_error:
        try:
            graph.query(
                queries.Q_RECORD_DECISION,
                {
                    "id": decision_id,
                    "action_type": req.action_type,
                    "subject": norm_subject,
                    "predicate": norm_predicate,
                    "requested_value": norm_value,
                    "decision": result.decision.value,
                    "reason_code": result.reason_code,
                    "policy_version": result.policy_version,
                    "created_at": now_iso,
                    "demo": demo_flag
                }
            )

            # Link evidence claims
            for ev in result.evidence:
                graph.query(
                    queries.Q_LINK_DECISION_EVIDENCE,
                    {
                        "decision_id": decision_id,
                        "claim_id": ev.claim_id,
                        "role": ev.role.value
                    }
                )
                relationships.append(
                    RelationshipItem(type="RELIED_ON", from_id=decision_id, to_id=ev.claim_id)
                )

            # Fetch relationships between evidence claims
            for ev in result.evidence:
                rel_rows = graph.query(queries.Q_GET_CLAIM_RELATIONSHIPS, {"id": ev.claim_id}).result_set
                for r in rel_rows:
                    relationships.append(RelationshipItem(type=r[0], from_id=r[1], to_id=r[2]))
        except Exception as e:
            print(f"Error persisting decision node: {e}")

    return VerifyResponse(
        decision_id=decision_id,
        decision=result.decision,
        reason_code=result.reason_code,
        policy_version=result.policy_version,
        governing_value=result.governing_value,
        evidence=result.evidence,
        relationships=relationships,
        remediation=result.remediation
    )

@app.get("/decisions", response_model=List[DecisionRecord])
def get_decisions():
    graph = db.get_graph()
    res = graph.query(queries.Q_GET_DECISIONS)
    records = []
    for row in res.result_set:
        d_id, act, subj, pred, req_val, dec, code, ver, created = row
        records.append(
            DecisionRecord(
                id=d_id,
                action_type=act,
                subject=subj,
                predicate=pred,
                requested_value=req_val,
                decision=DecisionType(dec),
                reason_code=code,
                policy_version=ver,
                created_at=created
            )
        )
    return records

@app.get("/stats", response_model=StatsResponse)
def get_stats():
    graph = db.get_graph()
    tot = graph.query(queries.Q_GET_STATS_TOTAL_CLAIMS).result_set[0][0]
    sup = graph.query(queries.Q_GET_STATS_SUPERSEDED_CLAIMS).result_set[0][0]
    disp = graph.query(queries.Q_GET_STATS_DISPUTED_CLAIMS).result_set[0][0]
    
    dec_rows = graph.query(queries.Q_GET_STATS_DECISIONS_BY_TYPE).result_set
    dec_dict = {"ALLOW": 0, "BLOCK": 0, "ABSTAIN": 0}
    for row in dec_rows:
        dtype, count = row
        dec_dict[dtype] = count

    return StatsResponse(
        total_claims=int(tot),
        superseded_claims=int(sup),
        disputed_claims=int(disp),
        decisions_by_type=dec_dict
    )

@app.post("/demo/seed")
def demo_seed():
    from recallguard.seed import seed_demo_scenario
    return seed_demo_scenario()

@app.post("/demo/reset")
def demo_reset():
    env = os.getenv("RECALLGUARD_ENV", "development")
    if env != "demo":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reset only permitted when RECALLGUARD_ENV=demo"
        )
    graph = db.get_graph()
    graph.query(queries.Q_DEMO_RESET)
    return {"status": "ok", "message": "Demo nodes cleaned successfully"}
