from pathlib import Path

from ..jobs import JobStore
from .media import ffprobe_duration, run_command

PADDING_SECONDS = 0.3
FADE_SECONDS = 0.02


def splice(job_id: str, store: JobStore) -> Path:
    review = store.read_json(job_id, "review")
    if not review:
        raise RuntimeError("review.json is required before splicing")
    original = store.original_path(job_id)
    if not original:
        original = materialize_original_audio(job_id, store)
    duration = ffprobe_duration(original)
    temp_dir = store.temp_dir(job_id)
    clips = []
    for index, segment in enumerate(review["ordered_segments"]):
        start = max(0.0, float(segment["start"]) - PADDING_SECONDS)
        end = min(duration, float(segment["end"]) + PADDING_SECONDS)
        if end <= start:
            continue
        clip = temp_dir / f"clip_{index:03d}.mp3"
        extract_clip(original, clip, start, end)
        clips.append(clip)

    if not clips:
        raise RuntimeError("no valid clips to splice")

    concat_file = temp_dir / "concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{clip.as_posix()}'" for clip in clips) + "\n", encoding="utf-8"
    )
    output = store.artifact_path(job_id, "output")
    run_command(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(output),
        ]
    )
    store.upload_media(job_id, output, content_type="audio/mpeg")
    return output


def materialize_original_audio(job_id: str, store: JobStore) -> Path:
    input_payload = store.read_json(job_id, "input") or {}
    filename = input_payload.get("original_filename")
    if not filename:
        raise RuntimeError("original audio is missing and input.json has no original filename")
    target = store.job_dir(job_id) / filename
    if store.download_media(job_id, filename, target):
        return target
    raise RuntimeError(f"original audio artifact is missing from storage: {filename}")


def extract_clip(source: Path, target: Path, start: float, end: float) -> None:
    duration = end - start
    fade_out_start = max(0.0, duration - FADE_SECONDS)
    run_command(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-to",
            f"{end:.3f}",
            "-i",
            str(source),
            "-af",
            f"afade=t=in:st=0:d={FADE_SECONDS},afade=t=out:st={fade_out_start:.3f}:d={FADE_SECONDS}",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(target),
        ]
    )
