# RecallGuard Progress Tracker

Project: **RecallGuard** — Graph-native memory verification and action gate for AI agents  
Hackathon: WeMakeDevs x FalkorDB Graph Hacks (Window closes Oct 18, 11:59 PM IST | Target: Oct 18, ~6 PM IST)  
Tracks: **Agent Memory and Coordination** (primary), **Agents That Act on Connected Data**  
Source of Truth: `SPEC.md`

---

## Milestones & Status

### Phase 1: Foundation & Verification
- [x] **Step a: Repo Structure & Tracking**
  - [x] Git repository initialized
  - [x] Directory structure defined (`backend/`, `eval/`, `frontend/`)
  - [x] `.gitignore`, `.env.example`, `docker-compose.yml` created
  - [x] `PROGRESS.md` and `DECISIONS.md` established
- [x] **Step b: FalkorDB Docker & Persistence Proof**
  - [x] FalkorDB running in Docker with persistent volume (`recallguard_falkordb_data:/var/lib/falkordb/data`)
  - [x] Verified connection and unique constraint syntax via official Python client (`falkordb 1.8.0`)
  - [x] Write-restart-read test proving persistence across container restart (`backend/tests/verify_falkordb_persistence.py`)
- [ ] **Step c: Architecture Plan Approval**
  - [ ] ≤15-line plan covering file layout, schema, and 8 policy rules presented
  - [ ] Human approval received

---

### Phase 2: Vertical Slice (Backend Core)
- [ ] **Cypher & Client Validation**
  - [ ] Verify FalkorDB Cypher syntax and Python client API against installed version
  - [ ] `backend/recallguard/queries.py` with named constants for every query
- [ ] **Idempotent Ingestion (`POST /claims`)**
  - [ ] Normalization (`subject`, `predicate`, `object`)
  - [ ] `source_claim_key` calculation & `MERGE` logic
  - [ ] `ASSERTED`, `PRODUCED`, `DERIVED_FROM` edge creation
  - [ ] Supersession rules (same (s,p), tier >= verified, tier >= old source tier)
  - [ ] Contradiction rules (failed supersession or conflicting value with no supersedes id)
- [ ] **Blast Radius (`GET /claims/{id}/blast-radius`)**
  - [ ] Multi-hop downstream derivation traversal (`<-[:DERIVED_FROM*1..5]-`)
  - [ ] Dependent earlier `ALLOW` decisions flagged `needs_review`
  - [ ] Auto-returned on supersession ingestion
- [ ] **Verification Policy (`POST /verify`)**
  - [ ] 8-rule deterministic policy (fail closed, stale dependency, supersession, conflict, allow)
  - [ ] Write `Decision` node and `RELIED_ON` edges
  - [ ] Return structured `evidence[]`, `relationships[]`, `remediation`
- [ ] **Supporting Endpoints**
  - [ ] `GET /health` (FalkorDB ping)
  - [ ] `GET /claims` and `GET /claims/{id}`
  - [ ] `GET /decisions`
  - [ ] `GET /stats` (real query counts: total, superseded, disputed, decisions by type)
  - [ ] `POST /demo/seed` and `POST /demo/reset` (demo-tagged nodes only)
- [ ] **Scenario E2E Automated Test (SPEC Section 7)**
  - [ ] Session 1: Config repo asserts staging (`c1`)
  - [ ] Session 1b: Agent A derives release plan (`c2`), ALLOW deploy staging
  - [ ] Session 2: Config repo supersedes with production (`c3`), blast radius includes `c2` & earlier ALLOW
  - [ ] Session 3: Agent B tries deploy staging -> BLOCK `REQUESTED_VALUE_SUPERSEDED`
  - [ ] Session 3b: Agent B tries action on `c2` -> BLOCK `STALE_DEPENDENCY`
  - [ ] Session 4: Deploy production -> ALLOW `MATCHES_CURRENT_EVIDENCE`
  - [ ] Bonus: Agent tier asserts canary -> CONTRADICTS, c3 unaffected (defeats "latest wins")

---

### Phase 3: Integration Surface
- [ ] `recallguard` Python client module (`guard.py`)
- [ ] `@guarded` decorator and simulated executor (`SIMULATED`)
- [ ] Demo script exercising `@guarded`

---

### Phase 4: Evaluation
- [ ] 30+ synthetic evaluation test cases in `eval/cases.py`
- [ ] Baseline implementation ("latest timestamp wins")
- [ ] Evaluation runner (`eval/run_eval.py`) reporting metrics (correct rate, false-ALLOW, false-BLOCK, ABSTAIN, latencies p50/p95)

---

### Phase 5: Frontend (Only after tests pass)
- [ ] Vite + React + TypeScript setup
- [ ] Demo Runner view (step-by-step with live decision cards and blast radius)
- [ ] Verification Playground (interactive form, color-coded decisions, evidence inspect)
- [ ] Memory Explorer (neighborhood Cytoscape.js visualization)
- [ ] Global Counters & Decision log

---

### Phase 6: Submission & Polish
- [ ] Comprehensive README with schema diagram, Cypher query citations, FalkorDB rationale, evaluation analysis, and AI assistance disclosure
- [ ] Demo video recording (2-3 min, explaining graph traversals)
- [ ] Final submission verification
