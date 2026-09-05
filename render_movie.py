#!/usr/bin/env python3
"""
SAME SEED narrated screenplay renderer.

Reads SAME_SEED.fountain, gives characters separate edge-tts voices, narrates
action text, creates SRT subtitles, and renders a 1080p H.264/AAC MP4.

Install:
    python -m pip install -r requirements-render.txt
    # also install ffmpeg so ffmpeg is on PATH

Try a preview:
    python render_movie.py --preview 40 --output same_seed_preview.mp4

Full render:
    python render_movie.py
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

try:
    import edge_tts
except ImportError:
    edge_tts = None

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = ImageDraw = ImageFont = None

try:
    from mutagen.mp3 import MP3
except ImportError:
    MP3 = None

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / ".same_seed_render"
DEFAULT_INPUT = ROOT / "SAME_SEED.fountain"
DEFAULT_OUTPUT = ROOT / "same_seed_movie.mp4"
W, H, FPS = 1920, 1080, 24

VOICE_PREFS = {
    "NARRATOR": ("en-US-ChristopherNeural", "Male"),
    "EVAN": ("en-US-GuyNeural", "Male"),
    "CLAIRE": ("en-US-JennyNeural", "Female"),
    "YOUNG CLAIRE": ("en-US-AriaNeural", "Female"),
    "MARA": ("en-US-AriaNeural", "Female"),
    "DIANE": ("en-US-JennyNeural", "Female"),
    "RAY": ("en-US-DavisNeural", "Male"),
    "JULIAN": ("en-US-EricNeural", "Male"),
    "JULIAN SYSTEM": ("en-US-AndrewNeural", "Male"),
    "AI": ("en-US-AndrewNeural", "Male"),
    "AI AGENT": ("en-US-AndrewNeural", "Male"),
    "SCREEN VOICE": ("en-US-AndrewNeural", "Male"),
    "CAR": ("en-US-AndrewNeural", "Male"),
    "DR. SHAH": ("en-US-MichelleNeural", "Female"),
    "INA": ("en-US-SaraNeural", "Female"),
    "ELLA": ("en-US-AnaNeural", "Female"),
    "TOMAS": ("en-US-TonyNeural", "Male"),
    "LIO": ("en-US-MichelleNeural", "Female"),
    "ATTENDANT": ("en-US-JennyNeural", "Female"),
    "DANIEL": ("en-US-ChristopherNeural", "Male"),
    "BEN": ("en-US-GuyNeural", "Male"),
}

KNOWN_SPEAKERS = {
    "EVAN", "EVAN (O.S.)", "EVAN (CONT'D)", "EVAN TEXT",
    "CLAIRE", "CLAIRE (CONT'D)", "YOUNG CLAIRE", "YOUNG CLAIRE (O.S.)",
    "MARA", "MARA (CONT'D)", "MARA (V.O.)", "MARA TEXT",
    "DIANE",
    "RAY", "RAY (CONT'D)", "RAY VOICE", "RAY VOICE (CONT'D)",
    "JULIAN", "JULIAN (CONT'D)", "JULIAN SYSTEM", "JULIAN SYSTEM (CONT'D)",
    "JULIAN SYSTEM (V.O.)", "JULIAN SYSTEM (VIDEO)",
    "AI", "AI AGENT", "CAR", "NARRATOR", "NARRATOR (V.O.)",
    "DOCTOR", "YOUNG COWORKER", "YOUNG COWORKER (CONT'D)", "WAITER",
    "COUNSELOR", "ATTORNEY", "STRANGER", "DR. SHAH", "DR. SHAH (CONT'D)",
    "AUDIENCE MEMBER", "DANIEL", "CHILD", "SPEECH THERAPIST",
    "SCREEN VOICE", "SCREEN VOICE (CONT'D)", "NEUROENGINEER",
    "NEUROENGINEER (CONT'D)", "BEN", "BEN (CONT'D)", "ELLA",
    "ATTENDANT", "ATTENDANT (CONT'D)", "INA", "INA (CONT'D)",
    "TOMAS", "TOMAS (CONT'D)", "LIO", "NEW WOMAN", "HER FRIEND",
    "WOMAN", "NURSE",
}


@dataclass
class Segment:
    index: int
    speaker: str
    text: str
    scene: str = ""
    voice: str = ""
    audio_file: str = ""
    duration: float = 0.0


def die(msg: str) -> None:
    print(f"\nERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)


def run(cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    print("$", " ".join(map(str, cmd)))
    return subprocess.run(
        cmd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def check_requirements() -> None:
    if not shutil.which("ffmpeg"):
        die("Install FFmpeg and put ffmpeg on PATH.")
    if edge_tts is None:
        die("Missing edge-tts. Run: python -m pip install -r requirements-render.txt")
    if Image is None:
        die("Missing Pillow. Run: python -m pip install -r requirements-render.txt")
    if MP3 is None:
        die("Missing mutagen. Run: python -m pip install -r requirements-render.txt")


def clean(text: str) -> str:
    text = text.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    text = text.replace("–", " - ")
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def speaker_base(cue: str) -> str:
    cue = re.sub(r"\s+\((?:O\.S\.|V\.O\.|CONT'D|VIDEO)\)\s*$", "", cue.strip())
    if cue.endswith(" TEXT"):
        cue = cue[:-5]
    if cue == "RAY VOICE":
        return "RAY"
    return cue


def speaker_cue(line: str) -> bool:
    return line.strip() in KNOWN_SPEAKERS


def split_long(text: str, limit: int = 900) -> list[str]:
    text = clean(text)
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        chunks = (
            textwrap.wrap(sentence, limit, break_long_words=False, break_on_hyphens=False)
            if len(sentence) > limit else [sentence]
        )
        for chunk in chunks:
            trial = (current + " " + chunk).strip()
            if current and len(trial) > limit:
                pieces.append(current)
                current = chunk
            else:
                current = trial
    if current:
        pieces.append(current)
    return pieces


def scene_spoken(line: str) -> str:
    s = line.lstrip(".").strip()
    s = re.sub(r"^INT\./EXT\.", "Interior exterior.", s, flags=re.I)
    s = re.sub(r"^INT\.", "Interior.", s, flags=re.I)
    s = re.sub(r"^EXT\.", "Exterior.", s, flags=re.I)
    return clean(s.replace(" - ", ". "))


def parse_screenplay(path: Path) -> list[Segment]:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i + 1 for i, x in enumerate(lines) if x.strip() == "==="), 0)
    out: list[Segment] = []
    scene = ""

    def add(who: str, text: str) -> None:
        for part in split_long(text):
            out.append(Segment(len(out), speaker_base(who), part, scene))

    i = start
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        if line.startswith("."):
            scene = line.lstrip(".").strip()
            add("NARRATOR", scene_spoken(line))
            i += 1
            continue

        if line.startswith(">"):
            directive = clean(line.lstrip(">").strip())
            if directive and directive != "SAME SEED":
                add("NARRATOR", directive)
            i += 1
            continue

        if line.startswith("#"):
            i += 1
            continue

        if speaker_cue(line):
            cue = line
            i += 1
            parts = []
            while i < len(lines):
                nxt = lines[i].strip()
                if not nxt or nxt.startswith((".", ">", "#")) or speaker_cue(nxt):
                    break
                if not (nxt.startswith("(") and nxt.endswith(")")):
                    parts.append(nxt)
                i += 1
            if parts:
                add(cue, " ".join(parts))
            continue

        parts = [line]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if not nxt or nxt.startswith((".", ">", "#")) or speaker_cue(nxt):
                break
            parts.append(nxt)
            i += 1
        add("NARRATOR", " ".join(parts))

    out.insert(0, Segment(0, "NARRATOR", "Same Seed.", "SAME SEED"))
    for n, seg in enumerate(out):
        seg.index = n
    return out


async def resolve_voices(speakers: Iterable[str]) -> dict[str, str]:
    voices = await edge_tts.list_voices()
    names = {v["ShortName"] for v in voices}
    pools = [
        [v for v in voices if v.get("Locale") == "en-US"],
        [v for v in voices if str(v.get("Locale", "")).startswith("en-")],
        voices,
    ]

    def fallback(gender: str) -> str:
        for pool in pools:
            for v in pool:
                if v.get("Gender") == gender:
                    return v["ShortName"]
        return voices[0]["ShortName"]

    result = {}
    for who in sorted(set(speakers)):
        preferred, gender = VOICE_PREFS.get(who, VOICE_PREFS["NARRATOR"])
        result[who] = preferred if preferred in names else fallback(gender)
    return result


def key_for(seg: Segment, rate: str) -> str:
    raw = f"{seg.voice}\0{rate}\0{seg.text}".encode()
    return hashlib.sha256(raw).hexdigest()[:20]


async def speak_one(seg: Segment, path: Path, rate: str) -> None:
    if path.exists() and path.stat().st_size > 1024:
        return
    last = None
    for attempt in range(1, 5):
        try:
            await edge_tts.Communicate(seg.text, seg.voice, rate=rate).save(str(path))
            if path.stat().st_size > 1024:
                return
            raise RuntimeError("tiny/empty TTS result")
        except Exception as exc:
            last = exc
            path.unlink(missing_ok=True)
            print(f"TTS retry {attempt}/4 on segment {seg.index}: {exc}")
            await asyncio.sleep(attempt * 2)
    raise RuntimeError(f"TTS failed on segment {seg.index}: {last}")


async def speak_all(segments: list[Segment], audio_dir: Path, rate: str, jobs: int) -> None:
    sem = asyncio.Semaphore(max(1, jobs))

    async def worker(seg: Segment) -> None:
        path = audio_dir / f"{seg.index:05d}_{key_for(seg, rate)}.mp3"
        seg.audio_file = str(path)
        async with sem:
            print(f"[{seg.index+1}/{len(segments)}] {seg.speaker}: {seg.text[:75]}")
            await speak_one(seg, path, rate)

    await asyncio.gather(*(worker(s) for s in segments))


def probe_duration(path: Path) -> float:
    """Read an MP3 duration directly instead of spawning ffprobe thousands of times."""
    return float(MP3(str(path)).info.length)


def stamp(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    h, r = divmod(ms, 3_600_000)
    m, r = divmod(r, 60_000)
    s, x = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{x:03d}"


def write_srt(segments: list[Segment], path: Path) -> float:
    now = 0.0
    rows = []
    for n, seg in enumerate(segments, 1):
        end = now + max(seg.duration, 0.25)
        body = seg.text if seg.speaker == "NARRATOR" else f"{seg.speaker}: {seg.text}"
        body = "\n".join(textwrap.wrap(body, 74, break_long_words=False))
        rows += [str(n), f"{stamp(now)} --> {stamp(end)}", body, ""]
        now = end
    path.write_text("\n".join(rows), encoding="utf-8")
    return now


def concat_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", r"'\''")


def concat_audio(segments: list[Segment], out: Path) -> None:
    listing = BUILD / "audio_concat.txt"
    listing.write_text(
        "\n".join(f"file '{concat_path(Path(s.audio_file))}'" for s in segments) + "\n",
        encoding="utf-8",
    )
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
        "-vn", "-c:a", "aac", "-strict", "-2", "-b:a", "160k", "-ar", "48000", str(out)
    ])


def font_file() -> str | None:
    for p in (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ):
        if p.exists():
            return str(p)
    return None


def background(path: Path) -> None:
    img = Image.new("RGB", (W, H), (9, 11, 16))
    d = ImageDraw.Draw(img)
    for y in range(H):
        v = int(9 + 14 * y / H)
        d.line((0, y, W, y), fill=(v, v + 2, v + 8))

    f = font_file()
    title = ImageFont.truetype(f, 94) if f else ImageFont.load_default()
    sub = ImageFont.truetype(f, 34) if f else ImageFont.load_default()
    foot = ImageFont.truetype(f, 25) if f else ImageFont.load_default()

    def centered(text: str, y: int, font, fill) -> None:
        box = d.textbbox((0, 0), text, font=font)
        d.text(((W - (box[2] - box[0])) / 2, y), text, font=font, fill=fill)

    centered("SAME SEED", 235, title, (238, 240, 245))
    centered("an original feature screenplay — narrated edition", 355, sub, (180, 187, 202))
    centered("The screenplay text appears as timed captions.", 965, foot, (110, 118, 135))
    img.save(path)


def subtitle_filter_path(path: Path) -> str:
    return path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")


def render_video(bg: Path, audio: Path, srt: Path, out: Path) -> None:
    # Keep the filter deliberately simple: very old FFmpeg/libass builds may not
    # understand modern force_style options, but can still render ordinary SRT.
    vf = f"subtitles='{subtitle_filter_path(srt)}'"
    run([
        "ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(bg),
        "-i", str(audio), "-vf", vf, "-c:v", "libx264",
        "-preset", "veryfast", "-tune", "stillimage", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-strict", "-2",
        "-b:a", "160k", "-ar", "48000",
        "-shortest", "-movflags", "+faststart", str(out)
    ])


async def build(args: argparse.Namespace) -> None:
    check_requirements()
    source = Path(args.input).resolve()
    out = Path(args.output).resolve()
    if not source.exists():
        die(f"Screenplay not found: {source}")
    out.parent.mkdir(parents=True, exist_ok=True)

    BUILD.mkdir(exist_ok=True)
    audio_dir = BUILD / "tts"
    if args.force and audio_dir.exists():
        shutil.rmtree(audio_dir)
    audio_dir.mkdir(exist_ok=True)

    segments = parse_screenplay(source)
    if args.preview:
        segments = segments[:args.preview]
    print(f"Parsed {len(segments)} spoken segments.")

    mapping = await resolve_voices(s.speaker for s in segments)
    for who, voice in sorted(mapping.items()):
        print(f"  {who:18s} -> {voice}")
    for seg in segments:
        seg.voice = mapping.get(seg.speaker, mapping["NARRATOR"])

    await speak_all(segments, audio_dir, args.rate, args.concurrency)

    for n, seg in enumerate(segments, 1):
        seg.duration = probe_duration(Path(seg.audio_file))
        if n % 250 == 0 or n == len(segments):
            print(f"Durations: {n}/{len(segments)}")

    srt = out.with_suffix(".srt")
    runtime = write_srt(segments, srt)
    (BUILD / "manifest.json").write_text(
        json.dumps([asdict(x) for x in segments], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Runtime: {runtime / 3600:.2f} hours")

    audio = BUILD / "same_seed_audio.m4a"
    concat_audio(segments, audio)

    if args.audio_only:
        target = out.with_suffix(".m4a")
        shutil.copy2(audio, target)
        print(f"DONE: {target}")
        print(f"SRT:  {srt}")
        return

    bg = BUILD / "background.png"
    background(bg)
    render_video(bg, audio, srt, out)
    print(f"DONE: {out}")
    print(f"SRT:  {srt}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Render SAME SEED as a narrated YouTube-ready MP4.")
    p.add_argument("--input", default=str(DEFAULT_INPUT))
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p.add_argument("--rate", default="+3%", help="edge-tts rate, e.g. +3%% or -5%%")
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--preview", type=int, default=0, help="only render first N spoken segments")
    p.add_argument("--force", action="store_true", help="regenerate cached TTS")
    p.add_argument("--audio-only", action="store_true")
    return p


def main() -> None:
    args = parser().parse_args()
    try:
        asyncio.run(build(args))
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run the same command to resume from cached TTS.")
        raise SystemExit(130)
    except subprocess.CalledProcessError as exc:
        die(f"Command failed ({exc.returncode}): {exc.cmd}")


if __name__ == "__main__":
    main()
