# Acceptance matrix

[Português](acceptance.pt-BR.md) · [Operations](reliability.md)

Automated acceptance means local verification without paid calls. Live validation
means execution against actual external services. They are different. Telegram authentication and permissions passed live read-only checks. Message
delivery and the model pilot still require deployment validation.

| Case | Automated evidence | Limitation / remaining live validation |
| --- | --- | --- |
| T01 | `test_resume_only_failed_metric_and_never_regenerate_saved_answer` | Simulated judge |
| T02 | Same test; independent metric checkpoints | Real RAGAs with a fake judge in integration tests |
| T03 | `test_invalid_metrics_cannot_be_successful` | Zero, missing and invalid values |
| T04 | `test_lock_rejects_second_process_before_call` | Actual local process locking |
| T05 | `test_configuration_model_prompt_and_corpus_are_fingerprinted` | External aliases still require pinned versions/providers |
| T06 | `test_hard_process_exit_between_metrics_is_resumable` | Actual local subprocess termination |
| T07 | `test_reconciliation_is_append_only_and_idempotent` | Fake metadata; no automatic reconciliation without an ID |
| T08 | `test_authorization_and_credit_errors_stop_after_one_case` and HTTP integration coverage | Live credentials will be checked after bot connection |
| T09 | `test_http_retry_and_budget_are_enforced_below_sdk` | Mock HTTP with a real client |
| T10 | Invalid extraction cache, answer/metric validation and four metrics with a fake judge | Real GLM truncation/empty output requires the pilot |
| T11 | `test_real_chroma_resume_uses_no_additional_embeddings` | Real Chroma, fake embeddings |
| T12 | `test_unusable_pdfs_fail_before_embedding_calls` and six-corpus preflight | Empty, invalid and textless PDFs; seven real books per RAG |
| T13 | `test_observed_index_dimension_mismatch_stops_before_embedding` | Real Chroma dimensions; live Neo4j not validated |
| T14 | Migration, corruption and legacy checks in `test_reliability.py` | Historical checkpoints have not been migrated in place |
| T15 | `test_pause_between_metrics_preserves_answer_and_first_metric` | VPS service not activated |
| T16 | Reservations, unknown usage, shared period and UTC rollover in `test_operations.py` | Financial reservation is an estimate, not a final billing guarantee |
| T17 | Queue idempotency and lost gateway response | Restart cannot requeue paid work |
| T18 | Authorization/injection checks in `test_operations.py` | Actual local Unix socket also tested |
| T19 | `test_delivery_failure_restart_and_deduplication` | Mock Telegram; live rate limits remain to be checked |
| T20 | Evidence/memory checks across six environments and supervisor watchdog | Real model limits and load require the pilot |
| T21 | Original CSV/error tests and persistent history | Publication reads only public artifacts |
| T22 | `test_scientific_repetition_has_an_independent_identity` | Statistical judge stability not measured yet |
| T23 | Gateway/notifier without model clients and a service without model keys | No external administrative-agent integration |
| T24 | `test_gateway_filters_chat_scope_and_persists_offset` and actual socket | Bot, channel and permission verified through the Bot API; live delivery not tested |
| T25 | `test_evaluation_mode_never_prepares_a_pipeline` and evidence export/import | A complete real frozen set still requires generated answers |
| T26 | `test_frozen_answer_missing_evidence_never_uses_reference` | Missing historical contexts are never fabricated |
| T27 | Pinned upstream prompt hashes and nine-column CSV for all six RAGs | Original-format CSV and Telegram bytes match |
| T28 | `test_knowledge_shared_history_survives_restart_and_metric_retry` | Shared last-five-exchange history persists without regenerating answers |
| T29 | Long duplicate document integration test in all six environments | No global deduplication or byte truncation |
| T30 | Shared CLI/bot flags, atomic batch queue and failure cancellation | Six sequential jobs; no shell execution |
| T31 | Original CSV plotting, frozen usage roundtrip and read-only channel check | Missing scores never become zeros; saved answer tokens remain intact |

## Protected data and audit

The hashes of all 25 checked files in this stage (datasets, historical results and
locks outside virtual environments) remained unchanged. Each of the six local
corpora contains seven PDFs and 2604 pages. This is 42 local PDF copies across six
architectures, not 42 different books.

Knowledge's corpus was copied from Context and verified by hash. Reproduce it with
`python -m app.tools.prepare_corpus`, which refuses to overwrite different files.
Notebook code comments were removed while saved outputs were preserved; previous
copies remain in the local stage backup. Notebooks remain historical material,
not supported entrypoints.

SSH inspection found the VPS repository at commit
`84fb661147a6dc68b97a6c50f3b2cd40b9fd7a0c`, with no Git changes and no container
listed by Docker Compose at that moment. Remote code, services and databases were
not changed in this stage. This observation does not replace a fresh, consistent
audit and backup immediately before deployment.

## Conditions for completing live validation

- Transfer the configured local `.env`, retaining VPS-specific endpoints; verify with `telegram-check`.
- Deploy code, environments and services. Current settings admit remote commands but never start a batch automatically.
- Verify permissions, dummy delivery, unauthorized-user rejection and outbox restart.
- Verify accounting and technical limits; the operator removed the monetary cap.
- Authorize a small batch through the bot and measure preparation, generation, judging and failures.
- Manually review evidence/scores and test actual pause/resume.
- Only then increase batch scope. The 6 × 90 benchmark was not executed in this
  stage; configuring `.env` does not start tests or send messages.

## Local verification results for this revision

- Root suite: 76 discovered tests, 65 passed, and 11 integration tests skipped
  because they belong to child environments. The actual Unix socket test passed.
- Integrations: 11 tests discovered in each environment; 56 passed and 10 were skipped
  because they target Knowledge or the plotting environment, without paid requests.
- Preflight: six passing corpora, seven PDFs and 2604 pages per corpus.
- Ruff, formatting and `git diff --check`: passed.
- Protected files: 25 hashes checked, no differences.
- Unified configuration: existing values preserved, mode `0600`, per-process
  credential filtering and execution without USD verified.
- All three systemd units passed syntax validation with local paths; their live
  permissions and execution still require VPS deployment.

Local logs: `tmp/implementation-20261007/`. The sandbox had blocked socket creation
and asynchronous shutdown; conclusive verification ran outside it, retaining fake
models/HTTP. No VPS service was activated.

Original protocol details and declared differences: [compatibility](compatibility.md).
Current logs: `unit-paper.log` and `integrations-paper.log` under the local log directory.

## Application layout and proxy verification — October 8, 2026

- `app/` now contains benchmark, dashboard, RAGs, providers, Telegram, services,
  tools, deployment, docs, scripts and tests. English READMEs are primary.
- General suite: 101 discovered, 90 passed, 11 integration tests skipped in the
  lightweight root environment. Those integrations passed in all six locked RAG
  environments, using fake model responses and real RAGAS/Chroma libraries.
- HTTP/proxy suite: 10 passed against an isolated Compose deployment, including
  authenticated WebSocket, rejection without login, logout and session persistence.
- Docker builds for dashboard/auth, worker and proxy succeeded. Isolated auth,
  dashboard, monitor, control and proxy became healthy. No benchmark was queued.
- Browser verification: login, reload without logout, execution form, original
  CSV chart and PNG/EPS downloads passed; no browser console errors were observed.
- Preservation check: 33 recorded historical/data/lock files and the plotting
  script retained their bytes; the same seven PDF hashes matched across six RAGs.
- Migration tests cover the approved code/path transition, refusal of changed
  configuration or unknown revisions, saved-answer/metric reuse, provenance and
  historical SQLite imports without duplicate experiment IDs.

The reported local WebSocket failure was reproduced as HTTP 403: the running
proxy retained an old Caddy configuration and sent an upgrade request to the HTTP
authentication endpoint. Copying the root Caddyfile into a locally built proxy image
makes configuration changes take effect through plain `docker compose up -d`.
After replacing only the local proxy, login returned 303, the page returned 200
and the authenticated WebSocket connected with the `streamlit` subprotocol.

These checks do not prove public TLS issuance, VPS resource capacity, live provider
availability or scientific scores. No paid model run or production graph rebuild
was performed as part of this reorganization. Historical files are preserved,
including invalid/incomplete sources that the dashboard explicitly flags.
