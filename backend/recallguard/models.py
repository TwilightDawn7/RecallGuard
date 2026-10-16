"""RecallGuard Pydantic models and data structures."""

from enum import Enum
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field

class ClaimStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"

class SourceType(str, Enum):
    CONFIG_FILE = "config_file"
    HUMAN = "human"
    AGENT = "agent"
    DOC = "doc"

class TrustTier(str, Enum):
    AUTHORITATIVE = "authoritative"
    VERIFIED = "verified"
    AGENT = "agent"
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        ranks = {
            TrustTier.AUTHORITATIVE: 3,
            TrustTier.VERIFIED: 2,
            TrustTier.AGENT: 1,
            TrustTier.UNKNOWN: 0,
        }
        return ranks[self]

class DecisionType(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    ABSTAIN = "ABSTAIN"

class EvidenceRole(str, Enum):
    GOVERNING = "governing"
    HISTORICAL = "historical"
    CHALLENGING = "challenging"
    TAINTED = "tainted"

# --- Ingestion Models ---

class SourceInput(BaseModel):
    id: str
    name: str
    source_type: SourceType = SourceType.AGENT
    trust_tier: TrustTier = TrustTier.UNKNOWN

class ClaimCreateRequest(BaseModel):
    claim_id: Optional[str] = None
    subject: str
    predicate: str
    object: str
    source: SourceInput
    session_id: str
    supersedes_claim_id: Optional[str] = None
    derived_from: Optional[List[str]] = Field(default_factory=list)

class ClaimResponse(BaseModel):
    id: str
    subject: str
    predicate: str
    object: str
    status: ClaimStatus
    created_at: str
    source_claim_key: str
    source: SourceInput
    session_id: str

# --- Blast Radius Models ---

class BlastRadiusClaim(BaseModel):
    id: str
    subject: str
    predicate: str
    object: str
    status: str
    depth: int

class BlastRadiusDecision(BaseModel):
    id: str
    action_type: str
    subject: str
    predicate: str
    requested_value: str
    decision: str
    created_at: str
    needs_review: bool = True

class BlastRadiusResponse(BaseModel):
    target_claim_id: str
    downstream_claims: List[BlastRadiusClaim]
    earlier_decisions: List[BlastRadiusDecision]
    summary: str

class ClaimIngestResponse(BaseModel):
    claim: ClaimResponse
    edges_created: List[str]
    superseded_claim_id: Optional[str] = None
    supersession_rejected: Optional[str] = None
    contradictions_created: List[str] = Field(default_factory=list)
    blast_radius: Optional[BlastRadiusResponse] = None

# --- Verification Models ---

class VerifyRequest(BaseModel):
    action_type: str
    subject: str
    predicate: str
    requested_value: str
    session_id: str

class EvidenceItem(BaseModel):
    claim_id: str
    role: EvidenceRole
    source_id: str
    source_name: str
    trust_tier: TrustTier
    status: ClaimStatus
    object: str

class RelationshipItem(BaseModel):
    type: str
    from_id: str
    to_id: str

class VerifyResponse(BaseModel):
    decision_id: str
    decision: DecisionType
    reason_code: str
    policy_version: str = "1.1"
    governing_value: Optional[str] = None
    evidence: List[EvidenceItem]
    relationships: List[RelationshipItem]
    remediation: str

# --- Stats & Decisions ---

class DecisionRecord(BaseModel):
    id: str
    action_type: str
    subject: str
    predicate: str
    requested_value: str
    decision: DecisionType
    reason_code: str
    policy_version: str
    created_at: str

class StatsResponse(BaseModel):
    total_claims: int
    superseded_claims: int
    disputed_claims: int
    decisions_by_type: Dict[str, int]
