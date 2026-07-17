import argparse
import os
import socket
import time
from dataclasses import dataclass
from uuid import uuid4

from .config import Settings, get_settings
from .jobs import JobStore
from .pipeline.runner import run_initial_pipeline, run_splice_pipeline
from .schemas import JobStatus


@dataclass
class Worker:
    settings: Settings
    store: JobStore
    worker_id: str

    @classmethod
    def create(cls) -> "Worker":
        settings = get_settings()
        store = JobStore(settings)
        worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
        return cls(settings=settings, store=store, worker_id=worker_id)

    def require_supabase(self) -> None:
        if not self.store.cloud:
            raise RuntimeError(
                "Worker polling requires STATE_BACKEND=supabase with SUPABASE_URL and "
                "SUPABASE_SERVICE_ROLE_KEY set. For local filesystem jobs, use "
                "`python -m podcast_editor.worker process <job_id> --stage initial`."
            )

    def poll_once(self, limit: int = 5) -> int:
        self.require_supabase()
        assert self.store.cloud is not None
        jobs = self.store.cloud.list_available_jobs(
            [JobStatus.queued, JobStatus.splicing], limit=limit
        )
        processed = 0
        for job in jobs:
            job_id = job["id"]
            status = JobStatus(job["status"])
            if status == JobStatus.queued:
                if not self.store.cloud.claim_job(
                    job_id, JobStatus.queued, self.worker_id, next_status=JobStatus.ingesting
                ):
                    continue
                source_url = job.get("source_url") or self.source_url_from_input(job_id)
                if not source_url:
                    self.store.set_status(
                        job_id,
                        JobStatus.error,
                        error="queued job has no source_url",
                        clear_lock=True,
                    )
                    continue
                run_initial_pipeline(job_id, source_url)
                processed += 1
            elif status == JobStatus.splicing:
                if not self.store.cloud.claim_job(job_id, JobStatus.splicing, self.worker_id):
                    continue
                run_splice_pipeline(job_id)
                processed += 1
        return processed

    def run_forever(self, limit: int = 5) -> None:
        self.require_supabase()
        print(f"worker {self.worker_id} polling every {self.settings.worker_poll_seconds}s")
        while True:
            processed = self.poll_once(limit=limit)
            if processed == 0:
                time.sleep(self.settings.worker_poll_seconds)

    def process_job(self, job_id: str, stage: str) -> None:
        if stage == "initial":
            source_url = self.source_url_from_input(job_id)
            if not source_url:
                raise RuntimeError("input.json must contain source_url for initial processing")
            run_initial_pipeline(job_id, source_url)
        elif stage == "splice":
            run_splice_pipeline(job_id)
        else:
            raise ValueError(f"unknown stage: {stage}")

    def source_url_from_input(self, job_id: str) -> str | None:
        payload = self.store.read_json(job_id, "input") or {}
        source_url = payload.get("source_url")
        return str(source_url) if source_url else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run podcast editor workers.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run", help="Poll Supabase for queued jobs.")
    run.add_argument("--once", action="store_true", help="Poll once and exit.")
    run.add_argument("--limit", type=int, default=5, help="Maximum jobs to claim per poll.")

    process = subcommands.add_parser("process", help="Process a known job id directly.")
    process.add_argument("job_id")
    process.add_argument("--stage", choices=["initial", "splice"], default="initial")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    worker = Worker.create()
    if args.command == "run":
        if args.once:
            processed = worker.poll_once(limit=args.limit)
            print(f"processed {processed} job(s)")
        else:
            worker.run_forever(limit=args.limit)
    elif args.command == "process":
        worker.process_job(args.job_id, args.stage)


if __name__ == "__main__":
    main()
