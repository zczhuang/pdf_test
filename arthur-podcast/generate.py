#!/usr/bin/env python3
"""
Arthur Podcast Generator
Generates audio podcasts from plain-text scripts using ElevenLabs TTS and pydub.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from pydub import AudioSegment
from pydub.effects import normalize


# ---------------------------------------------------------------------------
# Config & helpers
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
CHARACTERS_PATH = BASE_DIR / "characters.json"
SFX_DIR = BASE_DIR / "sfx"
MUSIC_DIR = BASE_DIR / "music"
CACHE_DIR = BASE_DIR / "cache"
OUTPUT_DIR = BASE_DIR / "output"


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def cache_key(text: str, voice_id: str) -> str:
    payload = f"{voice_id}::{text}"
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Script parsing
# ---------------------------------------------------------------------------

LINE_DIALOGUE = re.compile(r"^([A-Z][A-Z0-9 _'-]*):\s+(.+)$")
LINE_SFX      = re.compile(r"^\[SFX:\s*(.+?)\]$", re.IGNORECASE)
LINE_MUSIC    = re.compile(r"^\[MUSIC:\s*(.+?)\]$", re.IGNORECASE)
LINE_PAUSE    = re.compile(r"^\[PAUSE:\s*([\d.]+)\]$", re.IGNORECASE)


def parse_script(script_path: Path) -> list[dict]:
    """Return a list of instruction dicts parsed from the script file."""
    instructions = []
    with open(script_path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            if m := LINE_DIALOGUE.match(line):
                instructions.append({
                    "type": "dialogue",
                    "character": m.group(1).strip(),
                    "text": m.group(2).strip(),
                    "lineno": lineno,
                })
            elif m := LINE_SFX.match(line):
                instructions.append({"type": "sfx", "file": m.group(1).strip(), "lineno": lineno})
            elif m := LINE_MUSIC.match(line):
                val = m.group(1).strip()
                if val.lower() == "fade_out":
                    instructions.append({"type": "music_fade_out", "lineno": lineno})
                else:
                    instructions.append({"type": "music_start", "file": val, "lineno": lineno})
            elif m := LINE_PAUSE.match(line):
                instructions.append({"type": "pause", "seconds": float(m.group(1)), "lineno": lineno})
            else:
                print(f"WARNING line {lineno}: unrecognized format — {line!r}", file=sys.stderr)

    return instructions


def dry_run(instructions: list[dict]) -> None:
    print("\n=== DRY RUN — parsed script ===\n")
    for inst in instructions:
        t = inst["type"]
        ln = inst["lineno"]
        if t == "dialogue":
            print(f"  [{ln:4d}] DIALOGUE  {inst['character']}: {inst['text']}")
        elif t == "sfx":
            print(f"  [{ln:4d}] SFX       {inst['file']}")
        elif t == "music_start":
            print(f"  [{ln:4d}] MUSIC     start → {inst['file']}")
        elif t == "music_fade_out":
            print(f"  [{ln:4d}] MUSIC     fade_out")
        elif t == "pause":
            print(f"  [{ln:4d}] PAUSE     {inst['seconds']}s")
    print()


# ---------------------------------------------------------------------------
# TTS via ElevenLabs
# ---------------------------------------------------------------------------

def generate_voice_clip(text: str, voice_id: str, out_path: Path) -> None:
    """Call ElevenLabs API and save MP3 to out_path."""
    from elevenlabs.client import ElevenLabs

    config = load_json(CONFIG_PATH)
    api_key = config.get("elevenlabs_api_key", "")
    if not api_key or api_key == "your_key_here":
        raise ValueError(
            "ElevenLabs API key not set. Edit config.json and add your key."
        )

    client = ElevenLabs(api_key=api_key)

    audio_iter = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id="eleven_monolingual_v1",
        output_format="mp3_44100_128",
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        for chunk in audio_iter:
            f.write(chunk)


def get_voice_clip(text: str, voice_id: str) -> AudioSegment:
    """Return cached or freshly-generated voice clip as AudioSegment."""
    key = cache_key(text, voice_id)
    cached = CACHE_DIR / f"{key}.mp3"

    if cached.exists():
        return AudioSegment.from_mp3(cached)

    print(f"    → TTS: generating clip for voice {voice_id[:8]}…")
    generate_voice_clip(text, voice_id, cached)
    return AudioSegment.from_mp3(cached)


# ---------------------------------------------------------------------------
# Audio mixing helpers
# ---------------------------------------------------------------------------

DUCK_DB = 10   # how many dB to lower music during dialogue


def load_sfx(filename: str, sfx_volume_db: float) -> AudioSegment | None:
    for ext in ("", ".mp3", ".wav", ".ogg"):
        path = SFX_DIR / f"{filename}{ext}"
        if path.exists():
            seg = AudioSegment.from_file(path)
            return seg + sfx_volume_db
    print(f"WARNING: SFX file not found: {filename}", file=sys.stderr)
    return None


def load_music(filename: str) -> AudioSegment | None:
    for ext in ("", ".mp3", ".wav", ".ogg"):
        path = MUSIC_DIR / f"{filename}{ext}"
        if path.exists():
            return AudioSegment.from_file(path)
    print(f"WARNING: music file not found: {filename}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Main generation pipeline
# ---------------------------------------------------------------------------

def generate(script_path: Path) -> None:
    config      = load_json(CONFIG_PATH)
    characters  = load_json(CHARACTERS_PATH)

    music_vol_db      = float(config.get("music_volume_db", -18))
    sfx_vol_db        = float(config.get("sfx_volume_db", -10))
    pause_between_ms  = int(config.get("pause_between_lines_ms", 400))
    output_format     = config.get("output_format", "mp3")

    instructions = parse_script(script_path)
    if not instructions:
        print("No instructions found in script. Exiting.")
        return

    # We build the final mix track-by-track using two layers:
    #   • dialogue_track : voice + sfx segments placed sequentially
    #   • music_track    : looped music, ducked during dialogue
    # At the end we overlay them.

    dialogue_track = AudioSegment.silent(duration=0)
    music_track    = AudioSegment.silent(duration=0)

    current_music_raw: AudioSegment | None = None  # full-length raw music loop
    current_music_pos: int = 0                     # cursor into music track (ms)

    def extend_music_track(target_ms: int) -> None:
        """Extend music_track to reach target_ms, using current_music_raw."""
        nonlocal music_track, current_music_pos
        while len(music_track) < target_ms:
            if current_music_raw is None:
                music_track += AudioSegment.silent(duration=target_ms - len(music_track))
                break
            remaining = target_ms - len(music_track)
            music_chunk = current_music_raw[:remaining]
            # loop if needed
            while len(music_chunk) < remaining:
                music_chunk += current_music_raw
            music_chunk = music_chunk[:remaining]
            music_track += music_chunk + music_vol_db

    inter_pause = AudioSegment.silent(duration=pause_between_ms)

    for inst in instructions:
        t = inst["type"]

        if t == "dialogue":
            char = inst["character"]
            text = inst["text"]
            if char not in characters:
                print(f"WARNING: character {char!r} not in characters.json — skipping.", file=sys.stderr)
                continue
            voice_id = characters[char]
            if voice_id == "voice_id_here":
                raise ValueError(
                    f"Voice ID for {char} is still the placeholder. "
                    "Update characters.json with real ElevenLabs voice IDs."
                )

            print(f"  Generating voice for {char}…")
            clip = get_voice_clip(text, voice_id)
            clip_len = len(clip)

            # Extend music: ducked during dialogue
            dialogue_start = len(dialogue_track)
            if current_music_raw is not None:
                while len(music_track) < dialogue_start:
                    extend_music_track(dialogue_start)
                # add ducked music segment
                ducked_chunk = _music_chunk(current_music_raw, clip_len, music_vol_db - DUCK_DB)
                music_track += ducked_chunk

            dialogue_track += clip + inter_pause

            # Extend music for pause after dialogue (full volume)
            if current_music_raw is not None:
                pause_chunk = _music_chunk(current_music_raw, pause_between_ms, music_vol_db)
                music_track += pause_chunk

        elif t == "sfx":
            sfx = load_sfx(inst["file"], sfx_vol_db)
            if sfx:
                sfx_len = len(sfx)
                dialogue_track += sfx + inter_pause
                if current_music_raw is not None:
                    music_track += _music_chunk(current_music_raw, sfx_len + pause_between_ms, music_vol_db)

        elif t == "music_start":
            raw = load_music(inst["file"])
            if raw:
                current_music_raw = raw
                # Backfill music track to match dialogue_track length with silence
                while len(music_track) < len(dialogue_track):
                    music_track += AudioSegment.silent(duration=len(dialogue_track) - len(music_track))

        elif t == "music_fade_out":
            fade_ms = 3000
            if current_music_raw is not None:
                # Add a fading tail
                fade_chunk = _music_chunk(current_music_raw, fade_ms, music_vol_db).fade_out(fade_ms)
                music_track += fade_chunk
                dialogue_track += AudioSegment.silent(duration=fade_ms)
            current_music_raw = None

        elif t == "pause":
            silence_ms = int(inst["seconds"] * 1000)
            dialogue_track += AudioSegment.silent(duration=silence_ms)
            if current_music_raw is not None:
                music_track += _music_chunk(current_music_raw, silence_ms, music_vol_db)

    # Pad shorter track
    total_ms = max(len(dialogue_track), len(music_track))
    dialogue_track += AudioSegment.silent(duration=total_ms - len(dialogue_track))
    music_track    += AudioSegment.silent(duration=total_ms - len(music_track))

    print("\nMixing final audio…")
    final = music_track.overlay(dialogue_track)
    final = normalize(final)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = script_path.stem
    out_file = OUTPUT_DIR / f"{stem}.{output_format}"
    final.export(str(out_file), format=output_format)
    print(f"\nDone! Episode saved to: {out_file}")


def _music_chunk(raw: AudioSegment, duration_ms: int, vol_db: float) -> AudioSegment:
    """Return a looped+volume-adjusted slice of raw of exactly duration_ms."""
    chunk = raw[:duration_ms]
    while len(chunk) < duration_ms:
        chunk += raw
    return (chunk[:duration_ms]) + vol_db


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Arthur Podcast Generator — turns a script file into an MP3 episode."
    )
    parser.add_argument("script", type=Path, help="Path to the .txt script file")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print the script without calling any APIs",
    )
    args = parser.parse_args()

    if not args.script.exists():
        print(f"ERROR: Script file not found: {args.script}", file=sys.stderr)
        sys.exit(1)

    instructions = parse_script(args.script)

    if args.dry_run:
        dry_run(instructions)
        return

    generate(args.script)


if __name__ == "__main__":
    main()
