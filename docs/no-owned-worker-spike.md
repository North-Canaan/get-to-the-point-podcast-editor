# No-Owned-Worker Architecture Spike

This spike tests whether we can replace the Python/GPU worker architecture with managed async
services plus browser-side splicing.

## Hypothesis

The product can run without an owned long-running worker if:

1. AssemblyAI produces usable Hebrew transcripts with at least two speaker labels.
2. Highlight detection can consume the AssemblyAI transcript shape.
3. Browser-side `ffmpeg.wasm` can splice a realistic episode without unacceptable memory/runtime
   behavior.

## Test 1: AssemblyAI Hebrew Diarization

Run:

```bash
ASSEMBLYAI_API_KEY=... python scripts/spike_assemblyai.py \
  --audio-url "https://example.com/hebrew-episode.mp3"
```

Outputs:

```text
data/spikes/assemblyai-hebrew.json
data/spikes/assemblyai-hebrew-summary.json
```

Pass criteria:

- `language_code` is `he` or the transcript is clearly Hebrew.
- `speaker_count >= 2`.
- Segment timestamps are plausible.
- Hebrew text is good enough for highlight reasoning.

Fail criteria:

- No `utterances`.
- One speaker for a clear interview.
- Hebrew text quality is materially worse than WhisperX.
- AssemblyAI rejects the audio URL.

## Test 2: Browser ffmpeg.wasm Splice

Prepare a fixture:

```bash
python scripts/spike_browser_splice.py \
  --audio-url "https://example.com/hebrew-episode.mp3" \
  --segments 10:40 60:95
```

Open:

```text
public/splice-spike.html
```

Paste the fixture JSON and click **Run browser splice**.

Pass criteria:

- A downloadable MP3 is produced.
- Segment order is correct.
- No clipped words or obvious pops.
- Runtime and browser memory are acceptable on a normal laptop.

Fail criteria:

- Browser tab crashes or becomes unusable.
- ffmpeg.wasm fails on long source audio.
- Output has audible glitches that native ffmpeg did not have.

## Decision

If both tests pass on a realistic Hebrew episode, the no-owned-worker architecture is viable for a
v1 aimed at internal/editorial users.

If AssemblyAI Hebrew diarization fails, keep the Python worker with WhisperX/pyannote.

If browser splicing fails, keep server-side splicing even if transcription moves to AssemblyAI.
