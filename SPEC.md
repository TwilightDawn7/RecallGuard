# RecallGuard: SPEC

Graph-native memory verification and action gate for AI agents.
Built for WeMakeDevs x FalkorDB Graph Hacks. Window closes Oct 18, 11:59 PM IST. **Target submission: Oct 18, ~6 PM IST.**

Tracks to enter: **Agent Memory and Coordination** (primary), **Agents That Act on Connected Data**.

This file is the source of truth. If code and spec disagree, fix one and log it in `DECISIONS.md`.

---

## 0. Hard rules

1. FalkorDB is the primary store and does the reasoning (Cypher). No NetworkX, no in-memory graph engine for decisions.
2. Deterministic policy code decides ALLOW / BLOCK / ABSTAIN. No LLM in the decision path. No LLM at all in the MVP.
3. Any graph error means ABSTAIN (fail closed). Never ALLOW on error.
4. History is never overwritten. Supersession adds an edge and changes a status.
5. All data is synthetic and labeled so. No real or personal data.
6. Parameterized Cypher only. No Cypher accepted from the frontend.
7. Never fabricate results, metrics, IDs, or paths. Every ID and edge in a response must come from a query result.
8. Verify FalkorDB syntax and client methods against the installed version before using them. Do not guess.
9. Save every working Cypher query in `backend/recallguard/queries.py` (one named constant each) so the README can cite them.
10. Commit small and often. Keep `PROGRESS.md` and `DECISIONS.md` updated.

## 1. Why the graph is essential (the pitch the README must make)

Two traversals that a versioned table cannot do cleanly:

- **Provenance chain check:** a claim can be `DERIVED_FROM` other claims across agents. A claim is *tainted* if any ancestor (up to depth 5) is superseded. Verification walks this chain.
- **Blast radius:** when a claim is superseded, one multi-hop query returns every downstream claim and every earlier ALLOW decision that relied on it.

The demo must show both. Without them the product degrades to a lookup, and the rules say that is not enough.

## 2. Graph schema

Nodes (all have a stable string `id`):

| Label | Properties |
|---|---|
| `Claim` | id, subject, predicate, object, status, confidence?, created_at (UTC ISO), valid_from?, valid_until?, source_claim_key |
| `Source` | id, name, source_type (`config_file`, `human`, `agent`, `doc`), trust_tier |
| `Session` | id, agent_name, created_at |
| `Decision` | id, action_type, subject, predicate, requested_value, decision, reason_code, policy_version, created_at |

Claim `status` values:
- `active`: not superseded, not disputed.
- `superseded`: has an incoming `SUPERSEDES` edge.
- `disputed`: conflicts with a claim it could not legitimately supersede.

Relationships:

| Edge | Meaning |
|---|---|
| `(Source)-[:ASSERTED]->(Claim)` | who stated it |
| `(Session)-[:PRODUCED]->(Claim)` | which session ingested it |
| `(Claim)-[:SUPERSEDES {at, by_source}]->(Claim)` | explicit, authorized replacement |
| `(Claim)-[:CONTRADICTS]->(Claim)` | incompatible values, no valid supersession |
| `(Claim)-[:DERIVED_FROM]->(Claim)` | this claim was built from that one |
| `(Decision)-[:RELIED_ON {role}]->(Claim)` | claim used as evidence (`governing`, `historical`, `challenging`) |

Normalization: `subject` and `predicate` are lowercased, trimmed, whitespace to `_`. `object` is compared as a normalized string. Document this in code and README.

Trust tiers (numeric rank): `authoritative`=3, `verified`=2, `agent`=1, `unknown`=0.

Idempotency: `source_claim_key` = hash of (source_id, subject, predicate, object). Ingest with `MERGE` on it so repeated ingestion creates no duplicate. Add indexes on `Claim.id`, `Claim.subject`, `Source.id`, `Decision.id` (verify index syntax for the installed version; try constraints for uniqueness if supported).

## 3. Ingestion rules

`POST /claims` body: `claim_id?`, `subject`, `predicate`, `object`, `source {id, name, source_type, trust_tier}`, `session_id`, `supersedes_claim_id?`, `derived_from?: [claim_id]`.

1. Validate and normalize. `derived_from` and `supersedes_claim_id` must refer to existing claims, else 422.
2. `MERGE` Source, Session, Claim (by `source_claim_key`), then `ASSERTED` and `PRODUCED`.
3. Add `DERIVED_FROM` edges if given.
4. If `supersedes_claim_id` is given, create `SUPERSEDES` and set the old claim to `superseded` only if **all** hold:
   - old claim has the same subject and predicate,
   - new source tier >= `verified`,
   - new source tier >= tier of the old claim's source.
   Otherwise create `CONTRADICTS`, mark both `disputed`, and report `supersession_rejected: reason` in the response.
5. If no `supersedes_claim_id` is given and an active claim with the same subject and predicate has a different object, create `CONTRADICTS` and mark both `disputed`. **Never infer supersession from timestamps.**
6. Response includes the ids of everything created, plus `blast_radius` if a supersession happened (section 5).

## 4. Verification policy (version `1.0`)

Input: `action_type`, `subject`, `predicate`, `requested_value`, `session_id`.

Retrieve with Cypher: all claims for (subject, predicate), with source, supersession edges, contradiction edges, and the taint check on derivation ancestors.

Definitions:
- **Trusted claim:** status `active`, source tier >= `verified`, and not tainted.
- **Tainted:** any `DERIVED_FROM*1..5` ancestor has status `superseded`.
- **Governing value:** see rule 6 below.

Rules, evaluated in order. First match wins:

| # | Condition | Decision | reason_code |
|---|---|---|---|
| 1 | Graph query fails or times out | ABSTAIN | `GRAPH_UNAVAILABLE` |
| 2 | No claims for (subject, predicate) | ABSTAIN | `NO_EVIDENCE` |
| 3 | A claim equal to the requested value exists, is tainted, and no trusted claim equals it | BLOCK | `STALE_DEPENDENCY` |
| 4 | Requested value equals a `superseded` claim's object, and its superseder is trusted | BLOCK | `REQUESTED_VALUE_SUPERSEDED` |
| 5 | No trusted claims exist (only `agent`/`unknown` sources, or all disputed) | ABSTAIN | `INSUFFICIENT_TRUST` |
| 6 | Trusted claims disagree on value and the highest tier among them is not `authoritative` or has more than one distinct value at that tier | ABSTAIN | `UNRESOLVED_CONFLICT` |
| 7 | Governing value (the single value at the top tier, or the only trusted value) differs from the requested value | BLOCK | `REQUESTED_VALUE_CONFLICTS_CURRENT` |
| 8 | Governing value equals the requested value | ALLOW | `MATCHES_CURRENT_EVIDENCE` |

Every verification writes a `Decision` node with `RELIED_ON` edges to the claims it used. The response lists `evidence[]` (claim_id, role, source, tier, status) and `relationships[]` (type, from, to), all taken from query results. It also includes `remediation` (a short deterministic string, e.g. "Fetch the current value of application.deployment_target and retry").

Open question for the human (decide and log in `DECISIONS.md`): should a disputed claim from an `authoritative` source block actions on that subject? Default: yes, ABSTAIN with `UNRESOLVED_CONFLICT`.

## 5. Blast radius (the graph-native feature)

`GET /claims/{id}/blast-radius` returns:
- downstream claims: `MATCH (a:Claim {id:$id})<-[:DERIVED_FROM*1..5]-(d:Claim)`, distinct, with depth,
- earlier decisions that relied on the claim or any downstream claim, restricted to `decision = 'ALLOW'`, flagged `needs_review`.

Also returned automatically from `POST /claims` when a supersession is created. Show it in the UI as "This update invalidates N derived claims and M earlier ALLOW decisions".

## 6. API

| Endpoint | Purpose |
|---|---|
| `GET /health` | service and FalkorDB connectivity |
| `POST /claims` | ingest (section 3) |
| `GET /claims` | list with filters (subject, status) |
| `GET /claims/{id}` | claim with source, session, edges |
| `GET /claims/{id}/blast-radius` | section 5 |
| `POST /verify` | section 4 |
| `GET /decisions` | history |
| `GET /stats` | four counters from real queries: total claims, superseded, disputed, decisions by type |
| `POST /demo/seed` | seed the synthetic scenario |
| `POST /demo/reset` | only deletes nodes tagged `demo=true`, and only when `RECALLGUARD_ENV=demo` |

Pydantic models for every request and response. Verification never executes the action. A separate simulated executor in `guard.py` runs only on ALLOW and is labeled `SIMULATED`.

## 7. Demo scenario (synthetic, deterministic)

1. **Session 1 (agent A):** source `config_repo` (tier `authoritative`) asserts `application.deployment_target = staging` (claim `c1`).
2. **Agent A summary:** claim `c2` "release_plan.environment = staging", `DERIVED_FROM c1`, source `agent_a` (tier `verified`). An earlier `deploy staging` verification is ALLOWed, relying on `c1` and `c2`.
3. **Session 2:** `config_repo` asserts `deployment_target = production` (`c3`) with `supersedes_claim_id = c1`. Response shows the blast radius: `c2` plus the earlier ALLOW decision.
4. **Session 3 (agent B):** tries `deploy staging`, so BLOCK `REQUESTED_VALUE_SUPERSEDED`, evidence `c1`, `c3`, edge `SUPERSEDES`.
5. **Session 3b:** agent B tries to act on `c2` (derived, tainted), so BLOCK `STALE_DEPENDENCY`.
6. **Session 4:** `deploy production` gives ALLOW `MATCHES_CURRENT_EVIDENCE`.
7. Bonus beat: an `agent`-tier source claims `deployment_target = canary` with a newer timestamp and no supersession. It is recorded as `CONTRADICTS` and does not displace `c3`. A "latest wins" baseline would be fooled.

## 8. Evaluation

- Generate 30+ labeled cases from a table in code (`eval/cases.py`), not by hand-writing files. Categories: valid current, explicit supersession, unresolved conflict, missing provenance, wrong value, low-trust source, duplicate ingestion, historical claim, derived-claim taint, graph error (mock the client to raise).
- Each case builds its own small graph in a throwaway graph name, then calls `/verify`.
- Baseline: "latest-created claim for (subject, predicate); ALLOW iff it equals the requested value." Include cases where the baseline wins or ties, not only where it loses.
- Report: correct rate, false-ALLOW, false-BLOCK, ABSTAIN rate, supersession detection, p50 and p95 latency, per approach.
- README must state: the author wrote both rules and labels, the set is small and synthetic, and results show behavior on defined scenarios only.

## 9. Integration surface (P1, small)

`recallguard` Python client module with `guard.verify(...)` and a `@guarded(action_type, subject, predicate, value_arg)` decorator that calls `/verify` and runs the wrapped function only on ALLOW. The demo agent script uses this decorator. An MCP tool wrapper is optional.

## 10. Frontend (minimal, three views plus counters)

Use whatever the builder is fastest in (Vite + React is fine; Next.js only if already comfortable). Talk to the API only.

1. **Demo runner:** buttons for each session step; shows the live decision card and blast radius.
2. **Verification playground:** form to `/verify`; shows decision (distinct color per ALLOW, BLOCK, ABSTAIN; never green for BLOCK), reason, evidence list, relationships.
3. **Memory explorer:** claim list plus a selected-claim neighborhood (source, session, supersedes, contradicts, derived-from). Small graph view only for the selected neighborhood.
4. Four counters from `/stats` and a decisions table.

## 11. Tests

- Unit: normalization, policy function as a pure function over a fetched evidence structure (all 8 rules), validation.
- Integration (real FalkorDB): ingestion idempotency, supersession accept and reject, contradiction creation, blast radius, taint detection, Decision node writes.
- E2E: full scenario in section 7, then restart the API (and the FalkorDB container) and re-verify.
- Failure: stop or break the client and assert ABSTAIN `GRAPH_UNAVAILABLE`.

## 12. FalkorDB setup notes (verify, don't trust)

- Docker image `falkordb/falkordb`, port 6379. Mount a volume for data and enable persistence (AOF/RDB via the container's args or env). Confirm the data path and flags in the current FalkorDB docs, then prove persistence with a restart test.
- Python: `falkordb` client. Expected pattern is `FalkorDB(host, port)`, `db.select_graph(name)`, `graph.query(cypher, params)`. Confirm against the installed version.
- Use `MERGE` for idempotent writes and bound all variable-length paths (`*1..5`).

## 13. Schedule (IST)

| Window | Goal |
|---|---|
| Oct 16 (today) | Repo, Docker + volume, schema, ingestion, supersession, `/verify` with the 8-rule policy, blast radius, scenario E2E test green. Commit. |
| Oct 17 | Frontend (3 views + counters), `@guarded` decorator, eval harness and baseline, persistence test. |
| Oct 18 until 2 PM | Feature freeze at 2 PM. Bug fixes only afterward. |
| Oct 18, 2 to 6 PM | README (schema, Cypher, why FalkorDB, limitations, **AI assistance disclosure**), demo video (2 to 3 min), optional deploy, submit with both tracks named. |

## 14. Cut list

No LLM extraction, no multi-tenancy, no auth, no Redis or Kafka, no big graph visualization, no multi-agent framework, no live deployment unless everything else is done.

## 15. Tech stack (fixed unless I approve a change)

- Graph DB: FalkorDB (Docker, mounted volume for persistence)
- Backend: Python 3.11+, FastAPI, Pydantic v2, official `falkordb` client
- Tests: pytest, httpx / FastAPI TestClient
- Orchestration: Docker Compose (FalkorDB + API)
- Frontend: Vite + React + TypeScript, Tailwind; Cytoscape.js only for a selected claim's neighborhood
- Eval: plain Python script in eval/
- Not allowed: NetworkX, a second database, Redis, Kafka, LangChain, any LLM SDK, auth libraries
- Pin all versions after confirming them against the official docs

## 16. Submission checklist

- [ ] Public repo, commits spread across the event window
- [ ] README: setup, graph data model, schema diagram, core Cypher queries, why FalkorDB, evaluation and limitations
- [ ] AI assistance disclosure in README
- [ ] Demo video showing the working scenario and explaining the graph model and queries
- [ ] Local setup instructions that actually work from a clean clone
- [ ] Both tracks named in the submission form
- [ ] `.env.example`, synthetic-data labeling, no secrets
