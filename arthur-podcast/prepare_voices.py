#!/usr/bin/env python3
"""
prepare_voices.py — Download an Arthur episode, split it into segments,
label them by character, and merge into per-character voice files ready
for ElevenLabs Instant Voice Cloning.

Workflow:
  1. python prepare_voices.py download <URL>   ← download & split into segments
  2. python prepare_voices.py label            ← interactively label each segment
  3. python prepare_voices.py merge            ← merge into per-character .mp3 files
  4. python prepare_voices.py status           ← check labeling progress at any time

Then upload each voice_samples/final/<CHARACTER>.mp3 to ElevenLabs:
  Add Voice → Instant Voice Clone → upload file → save voice ID → paste into characters.json

Archive.org Season 1 episode URLs (copy one and pass to 'download'):
  S01E01: https://archive.org/download/Arthur_Season1/Arthur.S01E01.AMZN.WEBRip.H264-DVDRIP.mp4
  S01E02: https://archive.org/download/Arthur_Season1/Arthur.S01E02.AMZN.WEBRip.H264-DVDRIP.mp4
  (see https://archive.org/download/Arthur_Season1/ for the full file list)
"""

import argparse
import subprocess
import sys
from pathlib import Path

BASE_DIR       = Path(__file__).parent
SAMPLES_DIR    = BASE_DIR / "voice_samples"
UNLABELED_DIR  = SAMPLES_DIR / "unlabeled"
FINAL_DIR      = SAMPLES_DIR / "final"

CHARACTERS = ["ARTHUR", "DW", "TIMMY", "TOMMY", "NARRATOR"]

# Silence-split tuning
MIN_SILENCE_MS  = 500    # gap that counts as a speaker boundary
SILENCE_THRESH  = -40    # dBFS — raise if too many false splits
KEEP_SILENCE_MS = 150    # padding kept at each segment edge
MIN_SEG_MS      = 2_000  # discard clips shorter than 2 s
MAX_SEG_MS      = 25_000 # discard clips longer than 25 s (usually music/ambient)
TARGET_TOTAL_MS = 180_000  # target ~3 min per character for ElevenLabs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_dirs() -> None:
    for d in [UNLABELED_DIR, FINAL_DIR] + [SAMPLES_DIR / c for c in CHARACTERS]:
        d.mkdir(parents=True, exist_ok=True)


def _run(cmd: list[str], description: str = "") -> None:
    label = description or " ".join(cmd[:3])
    print(f"  [{label}]")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"ERROR: command failed (exit {result.returncode}): {' '.join(cmd)}", file=sys.stderr)
        sys.exit(1)


def _play(path: Path) -> bool:
    """Try common audio players; return True if one worked."""
    players = [
        ["mpg123", "-q"],
        ["mpg321", "-q"],
        ["afplay"],
        ["aplay", "-q"],
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
    ]
    for base in players:
        result = subprocess.run(base + [str(path)], capture_output=True)
        if result.returncode == 0:
            return True
    return False


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------

def cmd_download(url: str, keep_video: bool) -> None:
    ensure_dirs()
    video_path = SAMPLES_DIR / "episode_raw.mp4"
    audio_path = SAMPLES_DIR / "episode_audio.mp3"

    # ── 1. Download video ──────────────────────────────────────────────────
    print(f"\nDownloading episode from:\n  {url}\n")
    _run(
        [
            "yt-dlp",
            "--no-playlist",
            "--merge-output-format", "mp4",
            "-o", str(video_path),
            url,
        ],
        "yt-dlp download",
    )

    # ── 2. Extract audio ───────────────────────────────────────────────────
    print("\nExtracting audio track…")
    _run(
        [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vn",                  # drop video
            "-ac", "1",             # mono
            "-ar", "44100",         # 44 kHz
            "-ab", "192k",          # 192 kbps
            str(audio_path),
        ],
        "ffmpeg extract audio",
    )

    if not keep_video:
        video_path.unlink(missing_ok=True)
        print("  (video file removed — use --keep-video to retain it)")

    # ── 3. Split on silence ────────────────────────────────────────────────
    print("\nSplitting audio on silence…")
    _split_audio(audio_path)


def _split_audio(audio_path: Path) -> None:
    from pydub import AudioSegment
    from pydub.silence import split_on_silence

    audio = AudioSegment.from_mp3(str(audio_path))
    print(f"  Episode length: {len(audio)/60000:.1f} min")

    chunks = split_on_silence(
        audio,
        min_silence_len=MIN_SILENCE_MS,
        silence_thresh=SILENCE_THRESH,
        keep_silence=KEEP_SILENCE_MS,
        seek_step=10,
    )
    print(f"  Raw chunks found: {len(chunks)}")

    kept = [(i, c) for i, c in enumerate(chunks) if MIN_SEG_MS <= len(c) <= MAX_SEG_MS]
    print(f"  Usable (2–25 s): {len(kept)}")

    for rank, (original_idx, chunk) in enumerate(kept):
        out = UNLABELED_DIR / f"seg_{rank:04d}.mp3"
        chunk.export(str(out), format="mp3", bitrate="192k")

    print(f"\n✓ Saved {len(kept)} segments to voice_samples/unlabeled/")
    print("Next step:  python prepare_voices.py label\n")


# ---------------------------------------------------------------------------
# label
# ---------------------------------------------------------------------------

def cmd_label() -> None:
    ensure_dirs()
    segments = sorted(UNLABELED_DIR.glob("seg_*.mp3"))
    if not segments:
        print("No unlabeled segments found. Run 'download' first.")
        return

    char_map: dict[str, str] = {}
    for c in CHARACTERS:
        char_map[c.lower()] = c
        char_map[c[0].lower()] = c   # first-letter shortcut  e.g. 'a' → ARTHUR
    # DW is two letters; override 'd' to mean DW since there's no other D
    char_map["d"] = "DW"
    char_map["dw"] = "DW"
    char_map["n"] = "NARRATOR"

    print(f"\nLabeling {len(segments)} segments.")
    print(f"Characters : {', '.join(CHARACTERS)}")
    print("Shortcuts  : a=ARTHUR  d/dw=DW  ti=TIMMY  to=TOMMY  n=NARRATOR")
    print("             [s]kip  [q]uit\n")

    labeled = 0
    for seg in segments:
        played = _play(seg)
        if not played:
            print(f"  ► {seg.name}  (play manually — auto-play unavailable)")
        else:
            print(f"  ► {seg.name}")

        while True:
            raw = input("    Who is speaking? ").strip().lower()
            if raw in ("q", "quit"):
                print(f"\nPaused. {labeled} segments labeled so far.")
                print("Re-run 'label' to continue where you left off.")
                return
            if raw in ("s", "skip", ""):
                seg.rename(UNLABELED_DIR / f"_skip_{seg.name}")
                print("    skipped")
                break
            match = char_map.get(raw)
            if match:
                dest = SAMPLES_DIR / match / seg.name
                seg.rename(dest)
                labeled += 1
                print(f"    → {match}/")
                break
            # fuzzy: startswith
            candidates = [v for k, v in char_map.items() if k.startswith(raw)]
            if len(candidates) == 1:
                match = candidates[0]
                dest = SAMPLES_DIR / match / seg.name
                seg.rename(dest)
                labeled += 1
                print(f"    → {match}/")
                break
            print(f"    Unknown '{raw}'. Options: {', '.join(CHARACTERS)} or [s]kip")

    print(f"\n✓ Done! {labeled} segments labeled.")
    print("Next step:  python prepare_voices.py merge\n")


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------

def cmd_merge() -> None:
    from pydub import AudioSegment

    ensure_dirs()
    print("\nMerging labeled segments into per-character voice files…\n")

    any_merged = False
    for char in CHARACTERS:
        char_dir = SAMPLES_DIR / char
        segs = sorted(char_dir.glob("seg_*.mp3"))
        if not segs:
            print(f"  {char:<10} — no labeled segments, skipping")
            continue

        merged = AudioSegment.silent(duration=0)
        gap    = AudioSegment.silent(duration=400)
        used   = 0
        for p in segs:
            merged += AudioSegment.from_mp3(str(p)) + gap
            used += 1
            if len(merged) >= TARGET_TOTAL_MS:
                break

        out = FINAL_DIR / f"{char}.mp3"
        merged.export(str(out), format="mp3", bitrate="192k")
        mins = len(merged) / 60_000
        print(f"  {char:<10} — {used} clips  →  {mins:.1f} min  →  {out.name}")
        any_merged = True

    if any_merged:
        print(f"\n✓ Voice files saved to voice_samples/final/")
        print("\nUpload to ElevenLabs:")
        print("  1. Go to https://elevenlabs.io/app/voice-lab")
        print("  2. Click 'Add a new voice' → 'Instant Voice Clone'")
        print("  3. Upload the .mp3 file for each character")
        print("  4. Copy the generated voice ID")
        print("  5. Paste it into arthur-podcast/characters.json\n")
    else:
        print("\nNo characters have labeled segments yet. Run 'label' first.")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status() -> None:
    ensure_dirs()
    print("\n── Labeling status ──────────────────────────────────────────")
    unlabeled = sorted(UNLABELED_DIR.glob("seg_*.mp3"))
    skipped   = sorted(UNLABELED_DIR.glob("_skip_*.mp3"))
    print(f"  Unlabeled : {len(unlabeled)} segments remaining")
    print(f"  Skipped   : {len(skipped)}")
    print()

    for char in CHARACTERS:
        char_dir = SAMPLES_DIR / char
        segs = sorted(char_dir.glob("seg_*.mp3"))
        total_ms = 0
        for p in segs:
            try:
                from pydub import AudioSegment
                total_ms += len(AudioSegment.from_mp3(str(p)))
            except Exception:
                pass
        secs = total_ms / 1000
        bar  = "█" * min(int(secs / 10), 30)
        target_s = TARGET_TOTAL_MS / 1000
        status = "✓ ready" if secs >= 60 else ("needs more" if segs else "empty")
        print(f"  {char:<10} {len(segs):3d} clips  {secs:5.0f}s  {bar:<30} {status}")

    print()
    final_files = sorted(FINAL_DIR.glob("*.mp3"))
    if final_files:
        print(f"  Final voice files: {[f.name for f in final_files]}")
    else:
        print("  No final voice files yet (run 'merge' when ready)")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare per-character voice samples from Arthur episodes for ElevenLabs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Episode URLs (archive.org — free & legal):
  Season 1 directory: https://archive.org/download/Arthur_Season1/
  e.g. S01E01: https://archive.org/download/Arthur_Season1/Arthur.S01E01.AMZN.WEBRip.H264-DVDRIP.mp4

Quick start:
  python prepare_voices.py download "https://archive.org/download/Arthur_Season1/Arthur.S01E01.AMZN.WEBRip.H264-DVDRIP.mp4"
  python prepare_voices.py label
  python prepare_voices.py merge
        """,
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    dl = sub.add_parser("download", help="Download an episode and split into labeled segments")
    dl.add_argument("url", help="Direct URL to an Arthur episode video")
    dl.add_argument("--keep-video", action="store_true", help="Keep the raw video after audio extraction")

    sub.add_parser("label",  help="Interactively label unlabeled segments by character")
    sub.add_parser("merge",  help="Merge labeled segments into final per-character voice files")
    sub.add_parser("status", help="Show labeling progress per character")

    args = parser.parse_args()

    if args.command == "download":
        cmd_download(args.url, args.keep_video)
    elif args.command == "label":
        cmd_label()
    elif args.command == "merge":
        cmd_merge()
    elif args.command == "status":
        cmd_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
