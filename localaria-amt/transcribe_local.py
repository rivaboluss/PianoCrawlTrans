#!/usr/bin/env python3
"""Single-process Aria-AMT piano transcription. Works on Windows + CUDA."""

from __future__ import annotations

import argparse
import copy
import logging
import os
import sys
import traceback

import torch

from amt.audio import AudioTransform
from amt.config import load_model_config
from amt.data import get_wav_segments
from amt.inference.model import AmtEncoderDecoder, ModelConfig
from amt.inference.transcribe import (
    CHUNK_LEN_MS,
    LEN_MS,
    STRIDE_FACTOR,
    _get_silent_intervals,
    _process_silent_intervals,
    _shift_onset,
    _truncate_seq,
    process_segments,
)
from amt.tokenizer import AmtTokenizer
from amt.utils import _load_weight

AUDIO_EXTS = (".wav", ".mp3", ".mp4", ".flac", ".ogg", ".m4a", ".aac")

# Normalize curly/smart quotes that Windows filenames often use.
_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'",  # ‘
        "\u2019": "'",  # ’
        "\u201c": '"',  # “
        "\u201d": '"',  # ”
    }
)


def _normalize_path_text(path: str) -> str:
    return path.translate(_QUOTE_MAP)


def collect_audio_files(audio_path: str) -> list[str]:
    audio_path = _normalize_path_text(os.path.abspath(audio_path))
    # If typed path used straight quotes but file uses curly ones (or vice versa),
    # fall back to a quote-insensitive match in the parent directory.
    if not os.path.exists(audio_path):
        parent, name = os.path.split(audio_path)
        norm_name = _normalize_path_text(name).lower()
        if os.path.isdir(parent):
            for entry in os.listdir(parent):
                if _normalize_path_text(entry).lower() == norm_name:
                    audio_path = os.path.join(parent, entry)
                    break

    if os.path.isdir(audio_path):
        files = []
        for root, _, names in os.walk(audio_path):
            for name in names:
                if name.lower().endswith(AUDIO_EXTS):
                    files.append(os.path.join(root, name))
        return sorted(files)
    if os.path.isfile(audio_path):
        return [audio_path]
    return []


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
    )
    return logging.getLogger("aria-amt")


def load_model(model_name: str, checkpoint_path: str):
    tokenizer = AmtTokenizer()
    model_config = ModelConfig(**load_model_config(model_name))
    model_config.set_vocab_size(tokenizer.vocab_size)
    model = AmtEncoderDecoder(model_config)
    model_state = _load_weight(ckpt_path=checkpoint_path)

    cleaned = {}
    for key, value in model_state.items():
        if key.startswith("_orig_mod."):
            cleaned[key[len("_orig_mod.") :]] = value
        else:
            cleaned[key] = value
    model.load_state_dict(cleaned)

    cache_dtype = (
        torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float32
    )
    model.decoder.setup_cache(
        batch_size=1,
        max_seq_len=4096,
        dtype=cache_dtype,
    )
    model.cuda()
    model.eval()
    return model, tokenizer


def _peak_normalize(wav: torch.Tensor, peak: float = 0.95) -> torch.Tensor:
    m = wav.abs().max().clamp_min(1e-8)
    return wav * (peak / m)


def transcribe_one(
    model,
    tokenizer,
    audio_transform,
    file_path,
    logger,
    *,
    silence_filter: bool = True,
    silence_top_db: float = 45.0,
    normalize: bool = False,
):
    seq = [tokenizer.bos_tok]
    concat_seq = [tokenizer.bos_tok]
    idx = 0
    for curr_audio_segment in get_wav_segments(
        audio_path=file_path,
        stride_factor=STRIDE_FACTOR,
        pad_last=True,
    ):
        if normalize:
            curr_audio_segment = _peak_normalize(curr_audio_segment)

        init_idx = len(seq)
        silent_intervals = (
            _get_silent_intervals(curr_audio_segment, top_db=silence_top_db)
            if silence_filter
            else []
        )
        input_seq = copy.deepcopy(seq)
        results = process_segments(
            tasks=[((curr_audio_segment, seq), 0)],
            model=model,
            audio_transform=audio_transform,
            tokenizer=tokenizer,
            logger=logger,
        )
        seq = results[0]

        if silence_filter:
            seq_adj = _process_silent_intervals(
                seq, intervals=silent_intervals, tokenizer=tokenizer
            )
            if len(seq_adj) < len(seq) - 15:
                logger.info(
                    f"Removed tokens ({len(seq)} -> {len(seq_adj)}) "
                    f"in audio chunk {idx} according to silence"
                )
                seq = seq_adj

        try:
            next_seq = _truncate_seq(
                seq,
                CHUNK_LEN_MS,
                LEN_MS - CHUNK_LEN_MS,
            )
        except Exception:
            logger.info(
                f"Failed to reconcile sequences for audio chunk {idx}: {file_path}"
            )
            logger.debug(traceback.format_exc())
            try:
                seq = _truncate_seq(
                    input_seq,
                    CHUNK_LEN_MS - 2,
                    CHUNK_LEN_MS,
                )
            except Exception:
                seq = [tokenizer.bos_tok]
                logger.info("Failed to recover prompt, using default")
            else:
                logger.info("Recovered from previous prompt")
        else:
            if seq[-1] == tokenizer.eos_tok:
                logger.info(f"Seen eos_tok in audio chunk {idx}: {file_path}")
                seq = seq[:-1]

            concat_seq += _shift_onset(
                seq[init_idx:],
                idx * CHUNK_LEN_MS,
            )

            if len(next_seq) == 1:
                logger.info(f"Skipping silent audio chunk {idx}: {file_path}")
                seq = [tokenizer.bos_tok]
            else:
                seq = next_seq

        idx += 1

    return concat_seq


def save_midi(seq, save_path, tokenizer, logger):
    last_onset = None
    for tok in seq[::-1]:
        if type(tok) is tuple and tok[0] == "onset":
            last_onset = tok[1]
            break
    if last_onset is None:
        raise ValueError("No onset tokens found; audio may not contain piano notes")

    mid_dict = tokenizer.detokenize(tokenized_seq=seq, len_ms=last_onset)
    mid_dict.remove_redundant_pedals()
    mid = mid_dict.to_midi()
    parent = os.path.dirname(os.path.abspath(save_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    mid.save(save_path)
    logger.info(f"Saved MIDI: {save_path}")


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description="Local Aria-AMT piano transcription (Windows-friendly)"
    )
    parser.add_argument("audio", help="Audio file or directory")
    parser.add_argument(
        "-o",
        "--save_dir",
        default=os.path.join(root, "output"),
        help="Directory to save MIDI files",
    )
    parser.add_argument(
        "-c",
        "--checkpoint",
        default=os.path.join(
            root, "checkpoints", "piano-medium-double-1.0.safetensors"
        ),
    )
    parser.add_argument("-m", "--model_name", default="medium-double")
    parser.add_argument(
        "--no-silence-filter",
        action="store_true",
        help="Do not drop notes that fall in detected quiet intervals "
        "(helps when soft passages are wiped).",
    )
    parser.add_argument(
        "--silence-top-db",
        type=float,
        default=45.0,
        help="Silence threshold in dB below peak (default 45). "
        "Lower = less aggressive (e.g. 30).",
    )
    parser.add_argument(
        "--normalize",
        action="store_true",
        help="Peak-normalize each 30s chunk before inference "
        "(helps quiet middle sections).",
    )
    args = parser.parse_args()

    logger = setup_logging()

    if not torch.cuda.is_available():
        logger.error(
            "CUDA is required. Check the NVIDIA driver and that PyTorch was "
            "installed with a CUDA wheel (not CPU-only)."
        )
        sys.exit(1)

    if not os.path.isfile(args.checkpoint):
        logger.error(f"Checkpoint not found: {args.checkpoint}")
        sys.exit(1)

    files = collect_audio_files(os.path.abspath(args.audio))
    if not files:
        logger.error(f"No audio files found at {args.audio}")
        sys.exit(1)

    os.makedirs(args.save_dir, exist_ok=True)
    logger.info(f"GPU: {torch.cuda.get_device_name(0)}")
    logger.info(f"Loading model from {args.checkpoint}")
    model, tokenizer = load_model(args.model_name, args.checkpoint)
    audio_transform = AudioTransform().cuda()

    for file_path in files:
        logger.info(f"Transcribing: {file_path}")
        save_path = os.path.join(
            args.save_dir,
            os.path.splitext(os.path.basename(file_path))[0] + ".mid",
        )
        try:
            seq = transcribe_one(
                model,
                tokenizer,
                audio_transform,
                file_path,
                logger,
                silence_filter=not args.no_silence_filter,
                silence_top_db=args.silence_top_db,
                normalize=args.normalize,
            )
            if len(seq) < 10:
                logger.warning(f"Sequence too short, skip saving: {file_path}")
                continue
            save_midi(seq, save_path, tokenizer, logger)
        except Exception:
            logger.error(f"Failed: {file_path}\n{traceback.format_exc()}")

    logger.info("Done")


if __name__ == "__main__":
    main()
