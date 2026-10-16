# Architecture & Engineering Decisions (ADR)

This log documents all architectural decisions, trade-offs, and resolutions where code and spec align.

---

### ADR-001: Project Scope, Stack, and Source of Truth
- **Date:** 2026-10-16
- **Context:** We are building RecallGuard for the WeMakeDevs x FalkorDB Graph Hacks (closing Oct 18).
- **Decision:**
  - `SPEC.md` is the absolute source of truth.
  - Stack is fixed: Python 3.10+ with FastAPI, official `falkordb` client, Pydantic v2; Frontend in Vite + React + TypeScript with Tailwind CSS and Cytoscape.js for localized claim neighborhood views.
  - No auxiliary databases, no Redis/Kafka, no NetworkX, and no in-memory graph alternatives.
- **Rationale:** Keeps the architecture clean, fully graph-native, easily reproducible in Docker, and directly highlights FalkorDB's multi-hop query and graph capabilities.

---

### ADR-002: Graph Reasoning in Cypher vs. Deterministic Policy Code (Zero LLM in MVP)
- **Date:** 2026-10-16
- **Context:** Agent memory gating requires auditable, fast, predictable, and tamper-resistant decisions.
- **Decision:**
  - FalkorDB executes graph traversals (provenance chain check up to depth 5, blast radius calculation, contradiction discovery, and evidence gathering) entirely in Cypher.
  - A deterministic 8-rule policy evaluates the retrieved evidence into ALLOW, BLOCK, or ABSTAIN.
  - Zero LLMs are used in the verification loop.
  - Any graph timeout or error fails closed (ABSTAIN with `GRAPH_UNAVAILABLE`).
- **Rationale:** LLMs are non-deterministic, slow, hallucination-prone, and expensive for low-latency memory gating. FalkorDB Cypher provides sub-millisecond, verifiable graph traversal that can be defended mathematically and logically.

---

### ADR-003: Idempotency and Normalization Strategy
- **Date:** 2026-10-16
- **Context:** Multiple agents or retries may ingest the same claim repeatedly.
- **Decision:**
  - Normalization: `subject` and `predicate` are trimmed, lowercased, and whitespace replaced with `_`. `object` is stored and compared as a normalized string.
  - Idempotency key: `source_claim_key` = `sha256(f"{source_id}:{subject}:{predicate}:{object}")`.
  - Ingestion uses `MERGE (c:Claim {source_claim_key: $key})` so duplicate ingestion is a no-op update rather than creating duplicate graph entities.
- **Rationale:** Ensures clean graph topology without duplicate nodes for the same assertion from the same source.

---

### ADR-004: Handling Disputed Claims from Authoritative Sources
- **Date:** 2026-10-16
- **Context:** SPEC Section 4 poses an open question: should a disputed claim from an `authoritative` source block actions on that subject?
- **Decision:**
  - Default: Yes. The policy returns ABSTAIN with `UNRESOLVED_CONFLICT` (Rule 6).
- **Rationale:** If even authoritative sources are in a disputed state (e.g. conflicting values without valid supersession), the memory gate cannot safely guarantee correctness. Failing closed with ABSTAIN protects downstream systems from acting on conflicting data until clarified.

---

### ADR-005: FalkorDB Storage Persistence
- **Date:** 2026-10-16
- **Context:** FalkorDB runs as a Redis-module based graph engine in Docker. Graph state must survive container restarts.
- **Decision:**
  - Mount persistent volume to `/var/lib/falkordb/data`.
  - Pass `REDIS_ARGS=--save 60 1 --appendonly yes` to enable both periodic RDB snapshots and AOF journaling.
  - Verified persistence without manual `SAVE` across container restart; data durability is guaranteed via automatic AOF and Redis graceful shutdown on `SIGTERM`.
- **Rationale:** Guarantees durability across container restarts, stops, and rebuilds while maintaining low-latency in-memory query performance.

---

### ADR-006: Asymmetric Contradiction & Tier Protection
- **Date:** 2026-10-16
- **Context:** Low-trust agents or untrusted sources should not be able to disrupt or dispute claims asserted by authoritative or verified sources.
- **Decision:**
  - When claims conflict on value for the same `(subject, predicate)` without valid supersession:
    - If tiers differ: Create edge `(lower_tier_claim)-[:CONTRADICTS]->(higher_tier_claim)`. Mark ONLY the lower-tier claim as `disputed`. The higher-tier claim remains `active`.
    - If tiers are equal: Mark BOTH claims as `disputed`. Edge created `(new_claim)-[:CONTRADICTS]->(existing_claim)`.
- **Rationale:** A low-trust claim must never compromise an authoritative claim's active status.

---

### ADR-007: Multi-Hop Supersession Traversal (`SUPERSEDES*1..5`)
- **Date:** 2026-10-16
- **Context:** Claims may undergo multiple successive revisions (e.g. c1 -> c2 -> c3).
- **Decision:**
  - Verification Rule 4 traces multi-hop supersession `MATCH (head:Claim)-[:SUPERSEDES*1..5]->(old:Claim)` where `head.status = 'active'`. If the requested value matches an old superseded claim and its ultimate active head is trusted, rule 4 blocks with `REQUESTED_VALUE_SUPERSEDED`.
- **Rationale:** Prevents agents from acting on stale historical claims even when revisions occur across multiple sessions.

---

### ADR-008: Disputed Verified Claims Safety Check (Policy Rule 5)
- **Date:** 2026-10-16
- **Context:** If a verified claim has been contested by an equal or higher tier source, relying on it without superior governing authority is unsafe.
- **Decision:**
  - Before checking general trust availability, if any disputed claim with tier >= `verified` exists for the `(subject, predicate)` and no untainted trusted claim strictly outranks it, the policy immediately fails closed: `ABSTAIN` with `UNRESOLVED_CONFLICT`.
- **Rationale:** Maintains fail-closed safety when high-trust claims are actively disputed.

