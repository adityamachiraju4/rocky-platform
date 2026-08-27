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
- `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_TIMEOUT_SECONDS`

Speech:
- Local STT defaults to faster-whisper `small`.
- `LOCAL_WHISPER_LANGUAGE=auto` keeps automatic speech-language detection.
- Local English TTS defaults to Kokoro.
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
