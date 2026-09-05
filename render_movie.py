#!/usr/bin/env python3
"""
Render SAME_SEED.fountain as a narrated YouTube-ready MP4.

Pipeline:
1. Parse the Fountain screenplay into narration/dialogue segments.
2. Synthesize each segment with edge-tts using different character voices.
3. Probe durations with ffprobe and build a timed SRT.
4. Concatenate the speech into one AAC soundtrack with ffmpeg.
5. Render a 1920x1080 H.264/AAC MP4 with burned subtitles.

Requirements:
    pip install edge-tts pillow
    ffmpeg + ffprobe available on PATH

Examples:
    python render_movie.py
    python render_movie.py --preview 40
    python render_movie.py --force
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
from dataclasses import dataclass, asdict
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


ROOT = Path(__file__).resolve().parent
DEFAULT_SCREENPLAY = ROOT / "SAME_SEED.fountain"
DEFAULT_OUTPUT = ROOT / "same_seed_movie.mp4"
BUILD_DIR = ROOT / ".same_seed_render"

WIDTH = 1920
HEIGHT = 1080
FPS = 24

VOICE_PREFS = {
    "NARRATOR": ("en-US-ChristopherNeural", "Male"),
    "EVAN": ("en-US-GuyNeural", "Male"),
    "CLAIRE": ("en-US-JennyNeural", "Female"),
    "YOUNG CLAIRE": ("en-US-AriaNeural", "Female"),
    "MARA": ("en-US-AriaNeural", "Female"),
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
    "CLAIRE", "CLAIRE (CONT'D)", "YOUNG CLAIRE (O.S.)", "YOUNG CLAIRE",
    "MARA", "MARA (CONT'D)", "MARA (V.O.)", "MARA TEXT",
    "RAY", "RAY (CONT'D)", "RAY VOICE", "RAY VOICE (CONT'D)",
    "JULIAN", "JULIAN (CONT'D)", "JULIAN SYSTEM", "JULIAN SYSTEM (CONT'D)",
    "JULIAN SYSTEM (V.O.)", "JULIAN SYSTEM (VIDEO)",
    "AI", "AI AGENT", "CAR", "NARRATOR (V.O.)", "NARRATOR",
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


def die(message: str, code: int = 1) -> None:
    print(f"\nERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def run(cmd: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    print("$", " ".join(str(x) for x in cmd))
    return subprocess.run(
        cmd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def ensure_tools() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        die(
            "Missing required command(s): "
            + ", ".join(missing)
            + ". Install FFmpeg and ensure ffmpeg/ffprobe are on PATH."
        )
    if edge_tts is None:
        die("Python package 'edge-tts' is missing. Run: pip install edge-tts pillow")
    if Image is None:
        die("Python package 'Pillow' is missing. Run: pip install pillow")


def clean_fountain_text(text: str) -> str:
    text = text.replace("\u2014", " — ")
    text = text.replace("\u2013", " - ")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def spoken_scene_heading(line: str) -> str:
    s = line.lstrip(".").strip()
    s = re.sub(r"^INT\./EXT\.", "Interior exterior.", s, flags=re.I)
    s = re.sub(r"^INT\.", "Interior.", s, flags=re.I)
    s = re.sub(r"^EXT\.", "Exterior.", s, flags=re.I)
    s = s.replace(" - ", ". ")
    return clean_fountain_text(s)


def base_speaker(cue: str) -> str:
    cue = cue.strip()
    cue = re.sub(r"\s+\((?:O\.S\.|V\.O\.|CONT'D|VIDEO)\)\s*$", "", cue)
    if cue.endswith(" TEXT"):
        cue = cue[:-5]
    if cue == "RAY VOICE":
        return "RAY"
    if cue == "NARRATOR":
        return "NARRATOR"
    return cue


def is_speaker_cue(line: str) -> bool:
    return line.strip() in KNOWN_SPEAKERS


def split_long(text: str, limit: int = 900) -> list[str]:
    text = clean_fountain_text(text)
    if len(text) <= limit:
        return [text] if text else []

    sentences = re.split(r"(?<=[.!?])\s+", text)
    out, cur = [], ""
    for sent in sentences:
        if len(sent) > limit:
            pieces = textwrap.wrap(sent, width=limit, break_long_words=False, break_on_hyphens=False)
        else:
            pieces = [sent]
        for piece in pieces:
            candidate = (cur + " " + piece).strip()
            if cur and len(candidate) > limit:
                out.append(cur)
                cur = piece
            else:
                cur = candidate
    if cur:
        out.append(cur)
    return out


def parse_screenplay(path: Path) -> list[Segment]:
    raw_lines = path.read_text(encoding="utf-8").splitlines()

    start = 0
    for i, line in enumerate(raw_lines):
        if line.strip() == "===":
            start = i + 1
            break

    segments: list[Segment] = []
    scene = ""
    i = start

    def add(speaker: str, text: str, current_scene: str) -> None:
        for piece in split_long(text):
            if piece:
                segments.append(
                    Segment(
                        index=len(segments),
                        speaker=base_speaker(speaker),
                        text=piece,
                        scene=current_scene,
                    )
                )

    while i < len(raw_lines):
        line = raw_lines[i].strip()

        if not line:
            i += 1
            continue

        if line.startswith("."):
            scene = line.lstrip(".").strip()
            add("NARRATOR", spoken_scene_heading(line), scene)
            i += 1
            continue

        if line.startswith(">"):
            directive = clean_fountain_text(line.lstrip(">").strip())
            if directive and directive not in {"SAME SEED"}:
                add("NARRATOR", directive, scene)
            i += 1
            continue

        if line.startswith("#"):
            i += 1
            continue

        if is_speaker_cue(line):
            cue = line
            i += 1
            parts: list[str] = []
            while i < len(raw_lines):
                nxt = raw_lines[i].strip()
                if not nxt:
                    break
                if nxt.startswith(".") or nxt.startswith(">") or nxt.startswith("#") or is_speaker_cue(nxt):
                    break
                if not (nxt.startswith("(") and nxt.endswith(")")):
                    parts.append(nxt)
                i += 1
            if parts:
                add(cue, " ".join(parts), scene)
            continue

        parts = [line]
        i += 1
        while i < len(raw_lines):
            nxt = raw_lines[i].strip()
            if (
                not nxt
                or nxt.startswith(".")
                or nxt.startswith(">")
                or nxt.startswith("#")
                or is_speaker_cue(nxt)
            ):
                break
            parts.append(nxt)
            i += 1
        add("NARRATOR", " ".join(parts), scene)

    segments.insert(
        0,
        Segment(index=0, speaker="NARRATOR", text="Same Seed.", scene="SAME SEED"),
    )
    for idx, seg in enumerate(segments):
        seg.index = idx
    return segments


async def available_voices() -> list[dict]:
    return await edge_tts.list_voices()


async def resolve_voice_map(speakers: Iterable[str]) -> dict[str, str]:
    voices = await available_voices()
    names = {v["ShortName"] for v in voices}
    en_us = [v for v in voices if v.get("Locale") == "en-US"]
    any_en = [v for v in voices if str(v.get("Locale", "")).startswith("en-")]

    def fallback(gender: str) -> str:
        for pool in (en_us, any_en, voices):
            for v in pool:
                if v.get("Gender") == gender:
                    return v["ShortName"]
        return voices[0]["ShortName"]

    resolved: dict[str, str] = {}
    for speaker in sorted(set(speakers)):
        pref, gender = VOICE_PREFS.get(speaker, VOICE_PREFS["NARRATOR"])
        resolved[speaker] = pref if pref in names else fallback(gender)
    return resolved


def cache_key(seg: Segment, rate: str) -> str:
    payload = f"{seg.voice}\0{rate}\0{seg.text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


async def synthesize_one(seg: Segment, path: Path, rate: str, retries: int = 4) -> None:
    if path.exists() and path.stat().st_size > 1024:
        return
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            communicate = edge_tts.Communicate(seg.text, seg.voice, rate=rate)
            await communicate.save(str(path))
            if path.exists() and path.stat().st_size > 1024:
                return
            raise RuntimeError("TTS returned an empty/tiny audio file")
        except Exception as exc:
            last_error = exc
            if path.exists():
                path.unlink(missing_ok=True)
            print(f"TTS retry {attempt}/{retries} for segment {seg.index}: {exc}")
            await asyncio.sleep(min(8, attempt * 2))
    raise RuntimeError(f"TTS failed for segment {seg.index}: {last_error}")


async def synthesize_all(
    segments: list[Segment],
    audio_dir: Path,
    rate: str,
    concurrency: int,
) -> None:
    sem = asyncio.Semaphore(concurrency)

    async def worker(seg: Segment) -> None:
        key = cache_key(seg, rate)
        path = audio_dir / f"{seg.index:05d}_{key}.mp3"
        seg.audio_file = str(path)
        async with sem:
            print(f"[{seg.index + 1}/{len(segments)}] {seg.speaker}: {seg.text[:80]}")
            await synthesize_one(seg, path, rate)

    await asyncio.gather(*(worker(seg) for seg in segments))


def duration_seconds(path: Path) -> float:
    cp = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture=True,
    )
    return float(cp.stdout.strip())


def srt_timestamp(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def subtitle_text(seg: Segment) -> str:
    if seg.speaker == "NARRATOR":
        body = seg.text
    else:
        body = f"{seg.speaker}: {seg.text}"
    return "\n".join(textwrap.wrap(body, width=74, break_long_words=False))


def write_srt(segments: list[Segment], path: Path) -> float:
    t = 0.0
    lines: list[str] = []
    for idx, seg in enumerate(segments, 1):
        start = t
        end = t + max(seg.duration, 0.25)
        lines.extend(
            [
                str(idx),
                f"{srt_timestamp(start)} --> {srt_timestamp(end)}",
                subtitle_text(seg),
                "",
            ]
        )
        t = end
    path.write_text("\n".join(lines), encoding="utf-8")
    return t


def ffconcat_quote(path: Path) -> str:
    return str(path.resolve()).replace("'", r"'\''")


def concat_audio(segments: list[Segment], work: Path, out: Path) -> None:
    concat_file = work / "audio_concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{ffconcat_quote(Path(seg.audio_file))}'" for seg in segments) + "\n",
        encoding="utf-8",
    )
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "48000",
            str(out),
        ]
    )


def find_font() -> str | None:
    candidates = [
        ROOT / "DejaVuSans.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def create_background(path: Path) -> None:
    img = Image.new("RGB", (WIDTH, HEIGHT), (9, 11, 16))
    draw = ImageDraw.Draw(img)

    for y in range(HEIGHT):
        v = int(9 + 14 * (y / HEIGHT))
        draw.line([(0, y), (WIDTH, y)], fill=(v, v + 2, v + 8))

    font_path = find_font()
    if font_path:
        title_font = ImageFont.truetype(font_path, 94)
        sub_font = ImageFont.truetype(font_path, 34)
        tiny_font = ImageFont.truetype(font_path, 25)
    else:
        title_font = sub_font = tiny_font = ImageFont.load_default()

    title = "SAME SEED"
    bbox = draw.textbbox((0, 0), title, font=title_font)
    tw = bbox[2] - bbox[0]
    draw.text(((WIDTH - tw) / 2, 235), title, font=title_font, fill=(238, 240, 245))

    subtitle = "an original feature screenplay — narrated edition"
    bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
    sw = bbox[2] - bbox[0]
    draw.text(((WIDTH - sw) / 2, 355), subtitle, font=sub_font, fill=(180, 187, 202))

    footer = "The screenplay text appears as timed captions."
    bbox = draw.textbbox((0, 0), footer, font=tiny_font)
    fw = bbox[2] - bbox[0]
    draw.text(((WIDTH - fw) / 2, 965), footer, font=tiny_font, fill=(110, 118, 135))

    img.save(path, quality=95)


def ffmpeg_subtitle_path(path: Path) -> str:
    s = str(path.resolve()).replace("\\", "/")
    s = s.replace(":", r"\:")
    s = s.replace("'", r"\'")
    return s


def render_video(background: Path, audio: Path, srt: Path, output: Path) -> None:
    sub_path = ffmpeg_subtitle_path(srt)
    vf = (
        f"subtitles='{sub_path}':"
        "force_style='FontName=Arial,FontSize=30,"
        "PrimaryColour=&H00F4F4F4,OutlineColour=&H00101010,"
        "BorderStyle=3,Outline=1,Shadow=0,MarginV=72,Alignment=2'"
    )

    run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-i",
            str(background),
            "-i",
            str(audio),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "48000",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )


def write_manifest(segments: list[Segment], path: Path) -> None:
    path.write_text(
        json.dumps([asdict(s) for s in segments], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def async_main(args: argparse.Namespace) -> None:
    ensure_tools()

    screenplay = Path(args.input).resolve()
    output = Path(args.output).resolve()
    if not screenplay.exists():
        die(f"Screenplay not found: {screenplay}")

    BUILD_DIR.mkdir(exist_ok=True)
    audio_dir = BUILD_DIR / "tts"
    audio_dir.mkdir(exist_ok=True)

    if args.force and audio_dir.exists():
        print("Force mode: deleting cached TTS files.")
        shutil.rmtree(audio_dir)
        audio_dir.mkdir(exist_ok=True)

    segments = parse_screenplay(screenplay)
    if args.preview:
        segments = segments[: args.preview]

    print(f"Parsed {len(segments)} spoken segments.")

    voice_map = await resolve_voice_map(s.speaker for s in segments)
    print("\nVoice map:")
    for speaker, voice in sorted(voice_map.items()):
        print(f"  {speaker:18s} -> {voice}")

    for seg in segments:
        seg.voice = voice_map.get(seg.speaker, voice_map["NARRATOR"])

    await synthesize_all(
        segments,
        audio_dir=audio_dir,
        rate=args.rate,
        concurrency=max(1, args.concurrency),
    )

    print("\nProbing durations...")
    for n, seg in enumerate(segments, 1):
        seg.duration = duration_seconds(Path(seg.audio_file))
        if n % 50 == 0 or n == len(segments):
            print(f"  {n}/{len(segments)}")

    srt = output.with_suffix(".srt")
    total = write_srt(segments, srt)
    write_manifest(segments, BUILD_DIR / "manifest.json")
    print(f"Estimated runtime: {total / 3600:.2f} hours")
    print(f"Subtitles: {srt}")

    audio = BUILD_DIR / "same_seed_audio.m4a"
    concat_audio(segments, BUILD_DIR, audio)

    background = BUILD_DIR / "background.png"
    create_background(background)

    if args.audio_only:
        target = output.with_suffix(".m4a")
        shutil.copy2(audio, target)
        print(f"\nDONE: {target}")
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    render_video(background, audio, srt, output)
    print(f"\nDONE: {output}")
    print(f"SRT:  {srt}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Narrate SAME SEED and render a YouTube-ready MP4.")
    p.add_argument("--input", default=str(DEFAULT_SCREENPLAY), help="Input Fountain screenplay.")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output MP4 path.")
    p.add_argument(
        "--rate",
        default="+3%",
        help="edge-tts speech rate, e.g. '+3%%', '-5%%'. Default: +3%%",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Number of simultaneous TTS requests. Default: 3",
    )
    p.add_argument(
        "--preview",
        type=int,
        default=0,
        help="Render only the first N spoken segments for a quick test.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Delete cached TTS and regenerate everything.",
    )
    p.add_argument(
        "--audio-only",
        action="store_true",
        help="Stop after producing the full narrated M4A + SRT.",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nInterrupted. Cached TTS remains in .same_seed_render/; rerun to resume.")
        raise SystemExit(130)
    except subprocess.CalledProcessError as exc:
        die(f"External command failed with exit code {exc.returncode}: {exc.cmd}")


if __name__ == "__main__":
    main()
