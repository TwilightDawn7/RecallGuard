"""RecallGuard Policy Engine (Policy Version 1.1).

Evaluates retrieved graph evidence deterministically using Cypher traversal outputs.
Pure function logic with no LLMs.
"""

from typing import List, Dict, Optional, Tuple, Any
from recallguard.models import DecisionType, TrustTier, ClaimStatus, EvidenceRole, EvidenceItem

class PolicyEvaluationResult:
    def __init__(
        self,
        decision: DecisionType,
        reason_code: str,
        policy_version: str = "1.1",
        governing_value: Optional[str] = None,
        evidence: Optional[List[EvidenceItem]] = None,
        remediation: str = ""
    ):
        self.decision = decision
        self.reason_code = reason_code
        self.policy_version = policy_version
        self.governing_value = governing_value
        self.evidence = evidence or []
        self.remediation = remediation

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason_code": self.reason_code,
            "policy_version": self.policy_version,
            "governing_value": self.governing_value,
            "evidence": [e.model_dump() for e in self.evidence],
            "remediation": self.remediation
        }

class PolicyClaim:
    """In-memory representation of a claim and its graph context for policy evaluation."""
    def __init__(
        self,
        id: str,
        subject: str,
        predicate: str,
        object: str,
        status: str,
        source_id: str,
        source_name: str,
        trust_tier: str,
        is_tainted: bool = False,
        chain_head: Optional[Dict[str, Any]] = None
    ):
        self.id = id
        self.subject = subject
        self.predicate = predicate
        self.object = object
        self.status = ClaimStatus(status)
        self.source_id = source_id
        self.source_name = source_name
        self.trust_tier = TrustTier(trust_tier)
        self.is_tainted = is_tainted
        self.chain_head = chain_head  # If superseded, points to the active head claim

    @property
    def is_trusted(self) -> bool:
        """status active, source tier >= verified, and not tainted."""
        return (
            self.status == ClaimStatus.ACTIVE
            and self.trust_tier.rank >= TrustTier.VERIFIED.rank
            and not self.is_tainted
        )

def evaluate_policy(
    subject: str,
    predicate: str,
    requested_value: str,
    claims: List[PolicyClaim],
    graph_error: bool = False
) -> PolicyEvaluationResult:
    """
    Evaluates Policy 1.1 against graph evidence. Evaluated in order; first match wins:
    1. Graph query fails or times out -> ABSTAIN (GRAPH_UNAVAILABLE)
    2. No claims for (subject, predicate) -> ABSTAIN (NO_EVIDENCE)
    3. Requested value matches tainted claim and no trusted claim equals it -> BLOCK (STALE_DEPENDENCY)
    4. Requested value matches superseded claim whose chain head is trusted, and no trusted claim equals requested -> BLOCK (REQUESTED_VALUE_SUPERSEDED)
    5. A contested claim exists -> ABSTAIN (UNRESOLVED_CONFLICT)
    6. No trusted claims exist -> ABSTAIN (INSUFFICIENT_TRUST)
    7. Trusted claims disagree and top tier is not authoritative, or top tier has multiple distinct values -> ABSTAIN (UNRESOLVED_CONFLICT)
    8. Governing value differs from requested value -> BLOCK (REQUESTED_VALUE_CONFLICTS_CURRENT)
    9. Governing value equals requested value -> ALLOW (MATCHES_CURRENT_EVIDENCE)
    """

    # Rule 1: Graph failure or timeout
    if graph_error:
        return PolicyEvaluationResult(
            decision=DecisionType.ABSTAIN,
            reason_code="GRAPH_UNAVAILABLE",
            remediation="Check graph database connectivity and retry verification"
        )

    # Rule 2: No claims
    if not claims:
        return PolicyEvaluationResult(
            decision=DecisionType.ABSTAIN,
            reason_code="NO_EVIDENCE",
            remediation=f"Fetch the current value of {subject}.{predicate} and retry"
        )

    # Categorize claims
    trusted_claims = [c for c in claims if c.is_trusted]
    trusted_values = {c.object for c in trusted_claims}

    # Rule 3: Stale dependency (tainted claim matches requested value, not backed by any trusted claim)
    tainted_matches = [
        c for c in claims
        if c.object == requested_value and c.is_tainted
    ]
    if tainted_matches and requested_value not in trusted_values:
        evidence = [
            EvidenceItem(
                claim_id=c.id,
                role=EvidenceRole.TAINTED,
                source_id=c.source_id,
                source_name=c.source_name,
                trust_tier=c.trust_tier,
                status=c.status,
                object=c.object
            )
            for c in tainted_matches
        ]
        return PolicyEvaluationResult(
            decision=DecisionType.BLOCK,
            reason_code="STALE_DEPENDENCY",
            evidence=evidence,
            remediation=f"Dependency has been superseded; recompute {subject}.{predicate} from current sources"
        )

    # Rule 4: Requested value equals a superseded claim whose chain head is trusted
    for c in claims:
        if c.status == ClaimStatus.SUPERSEDED and c.object == requested_value:
            if requested_value not in trusted_values and c.chain_head:
                head_tier = TrustTier(c.chain_head.get("trust_tier", "unknown"))
                head_status = c.chain_head.get("status")
                if head_status == "active" and head_tier.rank >= TrustTier.VERIFIED.rank:
                    head_obj = c.chain_head.get("object")
                    evidence = [
                        EvidenceItem(
                            claim_id=c.id,
                            role=EvidenceRole.HISTORICAL,
                            source_id=c.source_id,
                            source_name=c.source_name,
                            trust_tier=c.trust_tier,
                            status=c.status,
                            object=c.object
                        ),
                        EvidenceItem(
                            claim_id=c.chain_head["id"],
                            role=EvidenceRole.GOVERNING,
                            source_id=c.chain_head.get("source_id", "head"),
                            source_name=c.chain_head.get("source_name", "head"),
                            trust_tier=head_tier,
                            status=ClaimStatus.ACTIVE,
                            object=head_obj
                        )
                    ]
                    return PolicyEvaluationResult(
                        decision=DecisionType.BLOCK,
                        reason_code="REQUESTED_VALUE_SUPERSEDED",
                        evidence=evidence,
                        remediation=f"Value is superseded by current evidence '{head_obj}'; update to latest value and retry"
                    )

    # Rule 5: Contested claim exists (disputed claim with tier >= verified, unless some trusted claim strictly outranks it)
    highest_trusted_rank = max([c.trust_tier.rank for c in trusted_claims], default=-1)
    contested_claims = [
        c for c in claims
        if c.status == ClaimStatus.DISPUTED
        and c.trust_tier.rank >= TrustTier.VERIFIED.rank
        and c.trust_tier.rank >= highest_trusted_rank
    ]
    if contested_claims:
        evidence = [
            EvidenceItem(
                claim_id=c.id,
                role=EvidenceRole.CHALLENGING,
                source_id=c.source_id,
                source_name=c.source_name,
                trust_tier=c.trust_tier,
                status=c.status,
                object=c.object
            )
            for c in contested_claims
        ]
        return PolicyEvaluationResult(
            decision=DecisionType.ABSTAIN,
            reason_code="UNRESOLVED_CONFLICT",
            evidence=evidence,
            remediation=f"Conflicting claims exist for {subject}.{predicate}; resolve source dispute before acting"
        )

    # Rule 6: No trusted claims exist
    if not trusted_claims:
        evidence = [
            EvidenceItem(
                claim_id=c.id,
                role=EvidenceRole.HISTORICAL if c.status == ClaimStatus.SUPERSEDED else EvidenceRole.CHALLENGING,
                source_id=c.source_id,
                source_name=c.source_name,
                trust_tier=c.trust_tier,
                status=c.status,
                object=c.object
            )
            for c in claims
        ]
        return PolicyEvaluationResult(
            decision=DecisionType.ABSTAIN,
            reason_code="INSUFFICIENT_TRUST",
            evidence=evidence,
            remediation=f"No verified or authoritative evidence for {subject}.{predicate}; ingest trusted source assertion"
        )

    # Determine top tier among trusted claims
    max_rank = max(c.trust_tier.rank for c in trusted_claims)
    top_tier_claims = [c for c in trusted_claims if c.trust_tier.rank == max_rank]
    distinct_top_values = list({c.object for c in top_tier_claims})

    # Rule 7: Trusted claims disagree and top tier is not authoritative, or top tier has multiple distinct values
    if len(distinct_top_values) > 1 or (len(trusted_values) > 1 and max_rank < TrustTier.AUTHORITATIVE.rank):
        evidence = [
            EvidenceItem(
                claim_id=c.id,
                role=EvidenceRole.CHALLENGING,
                source_id=c.source_id,
                source_name=c.source_name,
                trust_tier=c.trust_tier,
                status=c.status,
                object=c.object
            )
            for c in trusted_claims
        ]
        return PolicyEvaluationResult(
            decision=DecisionType.ABSTAIN,
            reason_code="UNRESOLVED_CONFLICT",
            evidence=evidence,
            remediation=f"Unresolved conflict among trusted sources for {subject}.{predicate}"
        )

    # Governing value is the single value at the top tier
    governing_value = distinct_top_values[0]
    governing_claims = [c for c in top_tier_claims if c.object == governing_value]

    evidence = [
        EvidenceItem(
            claim_id=c.id,
            role=EvidenceRole.GOVERNING,
            source_id=c.source_id,
            source_name=c.source_name,
            trust_tier=c.trust_tier,
            status=c.status,
            object=c.object
        )
        for c in governing_claims
    ]

    # Rule 8: Governing value differs from requested value
    if governing_value != requested_value:
        return PolicyEvaluationResult(
            decision=DecisionType.BLOCK,
            reason_code="REQUESTED_VALUE_CONFLICTS_CURRENT",
            governing_value=governing_value,
            evidence=evidence,
            remediation=f"Requested value '{requested_value}' conflicts with current governing value '{governing_value}'"
        )

    # Rule 9: Governing value equals requested value
    return PolicyEvaluationResult(
        decision=DecisionType.ALLOW,
        reason_code="MATCHES_CURRENT_EVIDENCE",
        governing_value=governing_value,
        evidence=evidence,
        remediation="Proceed with action"
    )
