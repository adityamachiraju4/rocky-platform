# Rocky API

FastAPI backend for Rocky's personal intelligence workspace. The API owns
identity, authentication, private capabilities, conversation orchestration,
local transcription, speech synthesis, and trusted live-information providers.

## Run Locally

```bash
cd /Users/adityamachiraju/rocky-platform/apps/api
cp .env.example .env
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The backend expects `SECRET_KEY` and `REFRESH_TOKEN_PEPPER` in `.env`. Database
configuration can use either `DATABASE_URL` or the `POSTGRES_*` variables.

## Tests

```bash
cd /Users/adityamachiraju/rocky-platform/apps/api
source .venv/bin/activate
pytest
```

Migration parity tests are marked separately and require `TEST_DATABASE_URL`.

## Migrations

```bash
cd /Users/adityamachiraju/rocky-platform/apps/api
source .venv/bin/activate
alembic current
alembic upgrade head
```

Run the Alembic commands whenever migrations change.

## Provider Configuration

Core assistant:
- `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_TIMEOUT_SECONDS`

`OPENAI_BASE_URL` is optional; unset or blank values preserve the OpenAI SDK
default endpoint. OpenAI-compatible providers such as OpenRouter can be
configured by setting `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL`.

Speech:
- Local STT defaults to faster-whisper `small`.
- `LOCAL_WHISPER_LANGUAGE=auto` keeps automatic speech-language detection.
- Local English TTS defaults to Kokoro.
- Local models load only in isolated child processes on first use, remain warm
  during activity, and their entire worker process becomes eligible for
  termination after `VOICE_MODEL_IDLE_SECONDS` (default 600). Terminating the
  process releases native model memory and thread pools from the container.
- `VOICE_MODEL_REAPER_SECONDS` controls idle checks (default 60); explicit
  `LOCAL_WHISPER_WARMUP=true` or `LOCAL_TTS_WARMUP=true` restores startup
  warm-up for development, still inside the child process.
- Worker lifecycle bounds are `VOICE_WORKER_STARTUP_SECONDS` (default 10),
  `VOICE_WORKER_REQUEST_SECONDS` (default 300), and
  `VOICE_WORKER_SHUTDOWN_SECONDS` (default 5). Local inference is serialized
  per model worker; crashes, malformed replies, EOF, and timeouts discard the
  affected worker so the next request starts a fresh process.
- OpenAI speech/STT keys are optional fallbacks and must remain server-side.

Live intelligence:
- Weather uses Open-Meteo and needs no key.
- Time uses Python `zoneinfo` and needs no key.
- News requires `NEWS_API_KEY`.
- Markets require `FINNHUB_API_KEY`.
- Current web search requires `TAVILY_API_KEY`.
- Places require `GEOAPIFY_API_KEY`.
- Sports uses `THESPORTSDB_API_KEY`; the public development key is suitable
  only for development.

When optional provider credentials are missing, Rocky should answer honestly
that the live provider is not configured. Never add fake data or expose
provider secrets to the frontend.

## Architecture Notes

Conversation keeps private Rocky capability execution separate from live
read-only tools:

```text
deterministic private capability
  -> live information intent
  -> general assistant fallback
```

The private capability registry remains the trust boundary for mutations.
Live tools have their own closed read-only registry and strict schemas.

## CI-6: query-specific personal context

Conversation starts with an empty WorldView. Deterministic zero-private-data
routes, live lookup, and durable live follow-ups run without personal domain
reads. A route classifier's personal-context decision only defers to the first
understanding pass; it cannot load a world itself. The first understanding pass
omits `rocky_world`. To answer from personal data, understanding must return:

```json
{
  "kind": "personal_context",
  "retrieval": {
    "query": "PhantomRed pricing",
    "scopes": ["notes", "lists"]
  }
}
```

This example omits the other nullable fields in the full provider schema.
`retrieval` is null for other result kinds. Its object forbids extra fields,
requires a nonblank string query of 1–200 characters, and requires 1–6 unique
scopes from `projects`, `tasks`, `reminders`, `notifications`, `notes`, `lists`.
The internal frozen `PersonalContextRequest` contains `kind`, `query`, and a
scope tuple. Missing retrieval, invalid scopes, duplicate scopes, wrong types,
and oversized queries raise `UnderstandingProviderError` and use the existing
provider-error fallback. Invalid requests never broaden into all-data retrieval.

Use the literal query `*` for an explicit overview of the requested categories
(e.g. current tasks or reminders). Other queries exclude records with no lexical
match. Rocky's `PersonalContextRetriever` calls authoritative domain services
with `current_user`; the provider never accesses repositories or the database.
Tasks require owned project enumeration, but project records only enter the
second-pass world if `projects` was requested. No other unrequested categories
are read. Existing ownership policies remain authoritative.

Ranking normalizes Unicode with NFKC, case-folds, and splits into word tokens.
Records sort lexicographically by exact normalized title, whole title phrase,
number of query tokens in the title, then number in body/context. Notes match
content, notifications match body, tasks match project name, and lists match
item content. Equal relevance favors active tasks/notes/lists, scheduled or due
reminders, and unread notifications. Reminder ties use earliest due time;
notification ties use newest creation time. Stable entity IDs break remaining
ties. Notes/lists include both active and archived records; reminders and
notifications include all supported statuses. Projects have no status signal
in WorldView. Task due dates and priorities are not available and are not inferred.
There are no temporal request hints or semantic/vector retrieval in v1.

Named retrieval limits are 8 records per scope, 48 top-level records total,
8 items per selected list (at most 64 items in addition to the 48 records),
200 characters per title/project name, 1,000 per note/notification/item content,
and 100 per timezone. List items sort by lexical relevance, active status, and
stable ID. Text truncation retains the deterministic leading prefix, so a match
late in a large body may be found without its full passage appearing in context.
`safe_world_payload()` independently caps broad worlds at 25 projects, 50 each
of tasks/reminders/notifications/notes, 25 lists with 8 items each, and the same
text limits. It serializes no ownership IDs or internal metadata; note content
is included so the second pass can answer about decisions.

The bounded retrieved world goes to exactly one second understanding pass.
A repeated personal-context request fails closed, including when retrieval was
empty. If that pass proposes an action, a plan requiring references, or a task
reference, complete owned grounding is loaded before validation/resolution.
Deterministic reference retries, action grounding, and plan preflight/execution
retain complete worlds to avoid hiding ambiguity. Existing recent turns,
grounded references, prior results, pending confirmation, activity recall,
place/live subject continuity, and temporal handling remain separate systems.

Retrieval logs contain requested scopes, selected counts, duration, and whether
a specific query or category overview was used. Invalid requests log a failure
classification. Queries and personal text are never logged by the retriever.
No database migration or public API change is required. Service-returned
candidates are scanned in memory; model context is bounded, but database reads
and scan cost are not bounded in v1. Large accounts may warrant paginated owned
search in a later iteration.

## CI-7: confidence-aware understanding recovery

CI-7 uses `Clarification` for insufficient information, without introducing a
global confidence score. Resolver ambiguity, TypeSafe/JEV confirmation
confidence, and independent plan-verification probabilities keep their existing
separate policies. `Unsupported` is terminal and never triggers recovery.

Understanding runs through explicit typed state tracking the pass kind
(normal, CI-6 retrieved, CI-7 recovery) and world source (none, complete trusted,
bounded retrieved). A turn has at most three understanding calls: one initial
call, at most one CI-6 retrieval-grounded call, and at most one CI-7 recovery
call. The loop is finite; there is no recursive retry. No retrieval is permitted
once a world is supplied or once recovery begins, including when the supplied
world is empty.

Recovery requires all of the following:

- The provider returned a valid `Clarification`.
- A world has already been authorized and supplied by CI-6 or a deterministic
  reference-dependent path.
- Recovery has not already been attempted for this turn.
- Already-loaded, owned, same-thread continuity adds information omitted from
  that provider pass: recent conversation turns or the prior message/reply.

Current grounded passes include durable entity references but omit recent-turn
continuity; recovery supplies that omitted continuity with the prior
clarification. Ungrounded passes return clarification directly. General
follow-ups already include continuity and receive no retry with identical
information. An already-supplied WorldView by itself does not justify another
call. Recovery adds no domain reads, complete-world loading, broader CI-6
scopes, embeddings/vector search, or live lookup. Conversation transcripts are
separate from retrieved records and remain untrusted data, never execution
authority. Recent continuity is capped again at 12 turns and 6,000 content
characters; the prior message and reply are each capped at 2,000 characters.

Clarification retains the public result shape: optional prompt (maximum 1,000
characters) and optional candidates (maximum 5 alternatives, each 1–200
characters). Five short options keep the existing concise clarification UX.
The provider JSON schema and strict Pydantic validation share these named
bounds. Clarification cannot carry non-null action, reference, arguments,
recall, plan, retrieval, conversation-reply or unsupported-reason fields.
Malformed results use `UnderstandingProviderError` and the existing
caller-specific fallback. Nullable unused fields remain valid. These provider
bounds do not truncate authoritative deterministic reference ambiguity.
A clarification without prompt or candidates gets a generic request to clarify.

The recovery provider is instructed to resolve only the missing distinction
from the additional context. Uncertain mutation intent, target, arguments or
plan steps/order must remain clarification; it must never choose a best guess.
A second clarification returns directly to the user; unsupported stays
unsupported; provider failure uses the existing fallback. A recovery request
for personal context is rejected without calling the retriever.

Unambiguous recovered action/plan proposals have no special authority. They go
through the same registry and argument validation, complete owned grounding
where required, reference resolution, plan compiler and preflight, independent
TypeSafe/JEV verification, confirmation policy and execution runtime. In
particular, partial CI-6 worlds are replaced with complete grounding before
reference-dependent action/plan resolution. Ambiguous mutation targets remain
non-executing. Pending confirmation, thread persistence, response language,
durable references, CI-5 temporal/live behavior and public API responses are
unchanged. No migration is required.

`understanding_recovery` logs only whether recovery was attempted, world-source
category, result kind, whether CI-6 retrieved context was present, candidate
count, elapsed time and fallback use. They contain no message, prompt,
candidate strings, retrieval query, or personal record contents.

V1 deliberately does not recover ungrounded clarification or repeat calls when
no additional authorized continuity is available. It does not introduce an
independent numerical confidence evaluator for understanding. The provider
must report remaining interpretation uncertainty as clarification; Rocky's
existing grounding and execution boundaries remain authoritative.

## CI-8 Phase A: explicit durable personal memory

Personal memory is a dedicated `personal_memories` domain, separate from Notes.
It stores only explicit user-requested facts/preferences (`kind=fact|preference`).
Subjects are nonblank and at most 255 characters; content is nonblank and at
most 2,000 characters, with matching or stricter domain/action/DB bounds.
Records carry an owned UUID, `user_id`, timestamps, `source=explicit_user`, and
optional conversation-thread provenance. The thread FK uses `ON DELETE SET
NULL`; removing a conversation never deletes the durable memory. User deletion
cascades to owned memories. No content uniqueness constraint is imposed.

Authenticated native endpoints:

- `POST /memories`: explicitly create a memory from kind/subject/content.
- `GET /memories`: list owned active memories, newest-updated first with stable
  timestamp/UUID tie breaks. `?status=forgotten` explicitly inspects forgotten
  records.
- `GET /memories/{memory_id}`: explicitly inspect an owned record.
- `PATCH /memories/{memory_id}`: update kind/subject/content or set
  `status=forgotten`. Empty updates, explicit nulls and extra authoritative
  fields are rejected. No DELETE endpoint is provided.

Every repository lookup includes both the memory UUID and current user UUID;
cross-owner reads/updates/forget requests return the same not-found response.
Lifecycle is irreversible `active → forgotten`. Forgetting sets `forgotten_at`
and retains the record for audit. Repeated authoritative forget is idempotent;
forgotten records cannot be updated or revived. Remembering again creates a
new record. Active and forgotten state/timestamp consistency is DB-constrained.

Four closed typed conversation actions use the existing registry/runtime:

- `memory.remember`: kind/subject/content only, low-risk write, no reference or
  confirmation. Ownership and thread provenance come from Rocky. Simple English
  explicit remember/save-as-preference/fact commands have a deterministic fast
  path that removes the command prefix, stores the requested fact, and uses
  `Personal fact`/`Personal preference` as a conservative subject rather than
  copying the private body into the activity label. Complex explicit requests
  may use registry-constrained provider interpretation.
- `memory.list`: no arguments/reference, read-only; active records only.
  Forgotten inspection is available through the explicit native status filter.
- `memory.update`: textual owned active reference plus at least one semantic
  change (kind/subject/content). No IDs, provenance, status or timestamps are
  provider arguments. Nullable unused optional plan arguments retain the
  existing provider-schema convention; native PATCH rejects explicit null.
- `memory.forget`: textual owned active reference, no arguments;
  destructive/reversal risk with `PLAN_STEP` confirmation.

Standalone forgetting becomes a narrowly validated internal one-forget
confirmation envelope and uses the existing durable pending-plan lifecycle,
compiler, preflight, independent TypeSafe/JEV verification, confirmation
classification and atomic claim/revalidation/execution. The model's public
plan contract still requires 2–8 steps; it cannot supply the internal envelope
marker. Multi-step memory plans use the ordinary compiler and compatible
result references. An owned active memory is resolved by exact subject first,
then unambiguous substring matching. Duplicates/missing/foreign targets never
mutate. UUID-shaped provider references are rejected. Old durable references
are checked against current active state and cannot revive forgotten records.
Forget results do not refresh durable active grounding.

Rocky checks explicit operation intent in the CURRENT request before accepting
memory proposals, including recovered proposals and plans. Mere statements
such as “I prefer dark mode” cannot authorize memory creation. Retrieved text,
past conversation, and provider assertions cannot grant that authority. V1's
conservative intent grammar supports English imperative forms; unsupported
wording requires clarification. Generic deterministic subjects can duplicate,
so later textual updates may require an explicit distinction or a grounded
“that memory” reference.

Activity events are `memory.remembered`, `memory.updated`, and
`memory.forgotten`. Payloads contain bounded kind/subject only, never the full
content field. Changed data emits one event; no-op updates and repeated forget
emit none. Memory plan failures log metadata rather than exception traces that
could contain private SQL parameters. Memory content is not logged by new
application code.

Phase A does not add memories to CI-6 `PersonalScope`, its retriever, normal
world loading, provider world serialization, or CI-7 recovery context. Active
memory references (ID/subject/kind/status, without content) are loaded only for
explicit reference-dependent memory actions/plans. Existing conversation
history remains a separate continuity system. CI-7's finite pass budget and
least-privilege retrieval behavior remain unchanged.

Revision `0017_personal_memory` follows `0016_conversation_pending_plans` and
creates the table, checks, ownership/status/update indexes and FKs. No automatic
extraction, implicit learning, inferred traits, embeddings/vector search,
confidence/salience scoring or proactive behavior is implemented. Automatic or
query-specific memory retrieval is future CI-8 Phase B, not Phase A.
