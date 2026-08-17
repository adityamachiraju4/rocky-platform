"""Generate temporary Rocky voice audition samples.

This is a developer utility, not an application endpoint. It writes mp3 files
outside the repository by default so auditions are not accidentally committed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import settings  # noqa: E402
from app.speech.openai_provider import ROCKY_VOICE_INSTRUCTIONS  # noqa: E402

AUDITION_TEXT = (
    "Hello. I'm Rocky. I'm here and ready when you are.\n\n"
    'Yesterday, you created the TestDev project and completed "Need to work '
    'on rocky."\n\n'
    'Done — I marked "Create the homepage" complete.'
)

DEFAULT_CANDIDATES: tuple[tuple[str, float], ...] = (
    ("cedar", 1.0),
    ("marin", 1.0),
    ("onyx", 1.0),
    ("ash", 1.0),
    ("echo", 1.0),
)


async def _generate(output_dir: Path) -> None:
    api_key = settings.get_openai_tts_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_TTS_API_KEY is not configured.")

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=settings.get_openai_tts_base_url(),
        timeout=settings.get_openai_timeout_seconds(),
    )
    model = settings.get_openai_tts_model()
    output_dir.mkdir(parents=True, exist_ok=True)

    for voice, speed in DEFAULT_CANDIDATES:
        response = await client.audio.speech.create(
            model=model,
            voice=voice,
            input=AUDITION_TEXT,
            instructions=ROCKY_VOICE_INSTRUCTIONS,
            response_format="mp3",
            speed=speed,
        )
        path = output_dir / f"rocky_{model}_{voice}_{speed:g}.mp3"
        path.write_bytes(response.content)
        print(f"{voice} speed={speed:g} -> {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        default=os.getenv(
            "ROCKY_VOICE_AUDITION_DIR",
            "/private/tmp/rocky-voice-audition",
        ),
    )
    args = parser.parse_args()
    asyncio.run(_generate(Path(args.output_dir)))


if __name__ == "__main__":
    main()
