#!/usr/bin/env python3
"""Thin CLI wrapper: MuScriptor piano transcription → MIDI."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"


def main() -> int:
    parser = argparse.ArgumentParser(description="Local MuScriptor transcription")
    parser.add_argument("audio", help="Input audio file")
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="Output .mid path (file, not directory)",
    )
    parser.add_argument(
        "-m",
        "--model",
        default="small",
        choices=["small", "medium", "large"],
        help="Model size (default small; GTX 1650 4GB 建议 small)",
    )
    parser.add_argument(
        "--instruments",
        default="acoustic_piano",
        help="Comma-separated instrument groups (default: acoustic_piano)",
    )
    parser.add_argument("--device", default="cuda")
    parser.add_argument(
        "--dtype",
        default="float16",
        help="float16 saves VRAM on consumer GPUs",
    )
    args = parser.parse_args()

    py = VENV_PY if VENV_PY.is_file() else Path(sys.executable)
    audio = Path(args.audio).resolve()
    out = Path(args.output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    if not audio.is_file():
        print(f"[error] audio not found: {audio}", file=sys.stderr)
        return 1

    cmd = [
        str(py),
        "-m",
        "muscriptor",
        "transcribe",
        str(audio),
        "-o",
        str(out),
        "--model",
        args.model,
        "--instruments",
        args.instruments,
        "--device",
        args.device,
        "--dtype",
        args.dtype,
    ]
    print("$ " + " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
