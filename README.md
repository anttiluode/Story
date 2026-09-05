# SAME SEED

A feature screenplay about an ordinary man who happens to live through the moment when artificial intelligence makes the nature of intelligence itself difficult to ignore.

## Read the screenplay

**[Read the complete screenplay — `SAME_SEED.fountain`](./SAME_SEED.fountain)**

The root Fountain file contains the entire feature in one document. The numbered files under `screenplay/` remain the gate-by-gate source chapters for editing and expansion.

## Render the whole screenplay as an MP4

`render_movie.py` turns the complete screenplay into a narrated 1080p audio-film suitable for uploading to YouTube.

It:

- reads `SAME_SEED.fountain`;
- narrates all action and scene text;
- gives Evan, Claire, Mara, Ray, Julian, the AI and several other characters different TTS voices;
- caches every spoken segment so an interrupted feature-length render can resume;
- creates a timed `.srt` subtitle file;
- concatenates the full soundtrack;
- burns the screenplay text over a clean 1920×1080 title background;
- outputs H.264/AAC `same_seed_movie.mp4` with `+faststart` for normal web/YouTube playback.

### Requirements

Install Python dependencies:

```bash
python -m pip install -r requirements-render.txt
```

Install **FFmpeg** separately and make sure both `ffmpeg` and `ffprobe` are available on your PATH.

### Test it first

Render only the first 40 spoken segments:

```bash
python render_movie.py --preview 40 --output same_seed_preview.mp4
```

### Render the full movie

```bash
python render_movie.py
```

The final files will be:

```text
same_seed_movie.mp4
same_seed_movie.srt
```

The hidden `.same_seed_render/` directory contains cached TTS segments and the intermediate soundtrack. If the process is interrupted, run the same command again and it will reuse completed speech. Use `python render_movie.py --force` only if you deliberately want to regenerate every voice segment.

You can also make only the narration soundtrack first:

```bash
python render_movie.py --audio-only
```

`edge-tts` requires an internet connection while the voices are being generated. The actual video encoding is local through FFmpeg.

The story begins as domestic drama and only gradually becomes speculative science fiction. AI is not the villain, the savior, or the cause of the protagonist's suffering. It is background pressure: a sequence of increasingly persuasive demonstrations that information, memory, perception, imagination, and identity may be less separate than people assumed.

The protagonist is fictional. His life is deliberately ordinary: work, marriage, parenthood, divorce, illness around him, aging, friendships, hobby projects, bad sleep, cheap computers, hospital rooms, and the stubborn human tendency to keep going.

## Structure

The screenplay is developed like an experimental program:

1. `STORY_BIBLE.md` — characters, rules, motifs, tone, ending logic.
2. `GATES.md` — dramatic gates. Each gate has a condition that must be earned before the story is allowed to escalate.
3. `screenplay/` — the chapter-by-chapter source, written in Fountain-compatible plain text.
4. `SAME_SEED.fountain` — the complete assembled feature screenplay in one file.

The core rule is simple: **no cosmic claim is allowed to arrive before the human story has paid for it.**

The final revelation is not that life was meaningless because it was artificial. It is almost the opposite: difficulty, uncertainty, mortality and incomplete knowledge are precisely the things the frictionless outer world cannot provide.

## Screenplay order

- `screenplay/00_TITLE.fountain`
- `screenplay/01_THE_TOY.fountain`
- `screenplay/02_RESOLUTION.fountain`
- `screenplay/03_LIFE_DOES_NOT_SCALE.fountain`
- `screenplay/04_THE_EMPTY_HOUSE.fountain`
- `screenplay/05_ENGRAMS.fountain`
- `screenplay/06_THE_QUIET_PART.fountain`
- `screenplay/07_THE_BORDER.fountain`
- `screenplay/08_LAST_ORDINARY_YEARS.fountain`
- `screenplay/09_THE_WAITING_ROOM.fountain`
- `screenplay/10_SAME_WORLD_NO_AI.fountain`

## Intent

Not *The Matrix*. No chosen one, no secret agents, no gun-fu, no computer terminal announcing the truth. The protagonist does not defeat reality or expose a conspiracy. He notices a pattern, doubts himself, lives anyway, and reaches the end with a question rather than proof.

Then the film answers him only after he can no longer use the answer.