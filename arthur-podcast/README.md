# Arthur Podcast Generator

Turn a plain-text script into a fully mixed MP3 episode — complete with character voices (via ElevenLabs), sound effects, background music, and volume ducking.

---

## Project Structure

```
arthur-podcast/
├── sfx/            ← Drop .mp3/.wav sound effect files here
├── music/          ← Drop .mp3/.wav background music files here
├── scripts/        ← Drop .txt episode script files here
├── output/         ← Finished MP3 episodes are saved here
├── cache/          ← Auto-generated voice clips (avoids re-calling the API)
├── characters.json ← Maps character names → ElevenLabs voice IDs
├── config.json     ← API key and default audio settings
├── generate.py     ← Main script
└── requirements.txt
```

---

## Script Format

Each line in a `.txt` script file must follow one of these formats:

| Format | Description |
|--------|-------------|
| `CHARACTER NAME: dialogue here` | Spoken line — character name must be ALL CAPS and match a key in `characters.json` |
| `[SFX: filename]` | Insert a sound effect from `/sfx` (omit extension) |
| `[MUSIC: filename]` | Start looping a background music track from `/music` |
| `[MUSIC: fade_out]` | Fade out the current background music |
| `[PAUSE: 1.5]` | Insert silence (value in seconds) |
| `# comment` | Lines starting with `#` are ignored |

### Example

```
[MUSIC: theme_song]
[PAUSE: 2]
NARRATOR: It was a perfectly ordinary Tuesday morning in Elwood City.
[SFX: birds_chirping]
ARTHUR: Ugh, I really don't want to go to school today.
DW: Too bad! Mom says you have to.
[PAUSE: 1]
ARTHUR: Fine. But I'm not happy about it.
[MUSIC: fade_out]
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** `pydub` requires `ffmpeg` to be installed on your system.
> - macOS: `brew install ffmpeg`
> - Ubuntu/Debian: `sudo apt install ffmpeg`
> - Windows: Download from https://ffmpeg.org/download.html and add to PATH

### 2. Create ElevenLabs voices

1. Go to [ElevenLabs Voice Design](https://elevenlabs.io/voice-lab)
2. Create a voice for each character (NARRATOR, ARTHUR, DW, TIMMY, TOMMY)
3. Copy each voice ID from the ElevenLabs dashboard

### 3. Configure characters.json

Replace the placeholder `voice_id_here` values with real ElevenLabs voice IDs:

```json
{
  "NARRATOR": "abc123...",
  "ARTHUR":   "def456...",
  "DW":       "ghi789...",
  "TIMMY":    "jkl012...",
  "TOMMY":    "mno345..."
}
```

### 4. Configure config.json

Add your ElevenLabs API key and adjust audio settings as desired:

```json
{
  "elevenlabs_api_key": "sk-...",
  "music_volume_db": -18,
  "sfx_volume_db": -10,
  "pause_between_lines_ms": 400,
  "output_format": "mp3"
}
```

| Setting | Description |
|---------|-------------|
| `music_volume_db` | Background music volume (negative = quieter; -18 is a good default) |
| `sfx_volume_db` | Sound effect volume relative to 0 dBFS |
| `pause_between_lines_ms` | Milliseconds of silence inserted after each dialogue line |
| `output_format` | Output file format (`mp3` or `wav`) |

### 5. Add audio files

- Drop sound effect files (`.mp3` or `.wav`) into `sfx/`
- Drop music files (`.mp3` or `.wav`) into `music/`
- Reference them in your script **without** the file extension

---

## Usage

### Dry run (no API calls — just parse and print the script)

```bash
python generate.py --dry-run scripts/episode1.txt
```

### Generate the episode

```bash
python generate.py scripts/episode1.txt
```

The finished MP3 will be saved to `output/episode1.mp3`.

---

## Caching

Generated voice clips are stored in `cache/` as `<sha256_hash>.mp3`.
The hash is computed from the **character voice ID + dialogue text**, so:
- Re-running the same script skips unchanged lines (no API cost)
- Changing a line's text or voice ID automatically regenerates that clip

To force a full regeneration, delete the contents of `cache/`.

---

## Volume Ducking

Background music is automatically ducked (lowered by 10 dB) while a character is speaking, then restored to its normal level during pauses and between lines. This keeps dialogue clear without silencing the atmosphere.

---

## Tips

- Character names in the script must **exactly** match keys in `characters.json` (case-sensitive, all caps)
- Music loops seamlessly — short clips work fine
- Use `[PAUSE: 0.5]` between scene transitions for a natural feel
- The `output/` and `cache/` directories are created automatically
