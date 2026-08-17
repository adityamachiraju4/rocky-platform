"""Generate temporary local Kokoro voice audition samples."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AUDITION_TEXT = (
    "Hello. I'm Rocky. I'm here and ready when you are.\n\n"
    'Yesterday, you created the TestDev project and completed "Need to work '
    'on rocky."\n\n'
    'Done — I marked "Create the homepage" complete.'
)

DEFAULT_CANDIDATES: tuple[tuple[str, float], ...] = (
    ("am_michael", 0.95),
    ("am_adam", 0.95),
    ("am_onyx", 0.95),
    ("am_echo", 0.95),
    ("af_heart", 0.95),
)


async def _generate(output_dir: Path) -> None:
    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline

    output_dir.mkdir(parents=True, exist_ok=True)
    pipeline_start = time.perf_counter()
    pipeline = KPipeline(lang_code="a")
    pipeline_ready = time.perf_counter()
    print(f"pipeline_load_seconds={pipeline_ready - pipeline_start:.3f}")

    for voice, speed in DEFAULT_CANDIDATES:
        started = time.perf_counter()
        generator = pipeline(AUDITION_TEXT, voice=voice, speed=speed)
        segments = [audio for _, _, audio in generator]
        generated = time.perf_counter()
        if not segments:
            print(f"{voice} speed={speed:g} failed=no_audio")
            continue

        audio = np.concatenate(segments)
        path = output_dir / f"rocky_kokoro_{voice}_{speed:g}.wav"
        sf.write(path, audio, 24000, format="WAV")
        duration = len(audio) / 24000
        print(
            f"{voice} speed={speed:g} "
            f"generation_seconds={generated - started:.3f} "
            f"audio_seconds={duration:.3f} -> {path}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        default=os.getenv(
            "ROCKY_LOCAL_VOICE_AUDITION_DIR",
            "/private/tmp/rocky-local-voice-audition",
        ),
    )
    args = parser.parse_args()
    asyncio.run(_generate(Path(args.output_dir)))


if __name__ == "__main__":
    main()
