from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .chunking import list_chunkers
from .config import AppConfig, BackendConfig, ENDPOINT_PROFILES, config_hash, load_run_config
from .formats import list_formats
from .languages import list_languages
from .output import export_csv, export_legacy_jsonl, export_parquet
from .pipeline import inspect_run, run_config_pipeline, validate_config_semantics
from .prompt_packs import list_prompt_packs


COMMANDS = {
    "run",
    "validate-config",
    "inspect",
    "list-formats",
    "list-languages",
    "list-source-languages",
    "list-chunkers",
    "list-prompt-packs",
    "list-endpoints",
    "export",
    "retry",
    "enqueue",
    "worker",
    "queue-status",
    "finalize",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llm-data-gen")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("run", "validate-config", "inspect", "enqueue"):
        command = subparsers.add_parser(name)
        command.add_argument("config", type=Path)

    retry = subparsers.add_parser("retry")
    retry.add_argument("config", type=Path)
    retry.add_argument(
        "--stages",
        default="generation,parsing",
        help="Comma-separated rejected stages to retry",
    )

    subparsers.add_parser("list-formats")
    subparsers.add_parser("list-languages")
    subparsers.add_parser("list-source-languages")
    subparsers.add_parser("list-chunkers")
    subparsers.add_parser("list-prompt-packs")
    subparsers.add_parser("list-endpoints")

    export = subparsers.add_parser("export")
    export.add_argument("dataset", type=Path)
    export.add_argument("output", type=Path)
    export.add_argument("--format", choices=("parquet", "csv", "legacy-jsonl"), required=True)

    worker = subparsers.add_parser("worker")
    worker.add_argument("--workers", type=int, default=None)

    status = subparsers.add_parser("queue-status")
    status.add_argument("run")

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("run")
    return parser


def build_legacy_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llm-data-gen")
    parser.add_argument("--input", required=True, type=Path, help="Input document or JSONL corpus")
    parser.add_argument("--output", default=Path("output/generated_examples.jsonl"), type=Path)
    parser.add_argument("--source-id", default=None)
    parser.add_argument("--source-language", required=True)
    parser.add_argument("--chunk-size", default=1200, type=int)
    parser.add_argument("--backend-name", default="llama-swap")
    parser.add_argument("--backend-base-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--backend-model", default="qwen38-27b-chat-rocmfp4")
    parser.add_argument("--backend-api-key", default=None)
    parser.add_argument("--resume", dest="resume", action="store_true")
    parser.add_argument("--no-resume", dest="resume", action="store_false")
    parser.set_defaults(resume=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] not in COMMANDS and arguments[0] != "--help":
        _legacy_main(arguments)
        return

    parser = build_parser()
    args = parser.parse_args(arguments)
    if args.command in {"run", "retry", "validate-config", "inspect", "enqueue"}:
        config_path = args.config.resolve()
        config = load_run_config(config_path)
        search_root = config_path.parent
        validate_config_semantics(config, search_root=search_root)

    if args.command == "run":
        rows = run_config_pipeline(config, search_root=search_root)
        print(
            json.dumps(
                {
                    "generated_this_invocation": len(rows),
                    "output_directory": str(config.output.directory),
                },
                indent=2,
            )
        )
    elif args.command == "retry":
        retry_stages = {stage.strip() for stage in args.stages.split(",") if stage.strip()}
        rows = run_config_pipeline(
            config,
            search_root=search_root,
            retry_stages=retry_stages,
        )
        print(
            json.dumps(
                {
                    "retried_stages": sorted(retry_stages),
                    "generated_this_invocation": len(rows),
                    "output_directory": str(config.output.directory),
                },
                indent=2,
            )
        )
    elif args.command == "enqueue":
        from .queueing import mark_enqueued, prepare_queued_run, task_payload, unfinished_job_ids

        try:
            from .queue_huey import enqueue_payload
        except ModuleNotFoundError as exc:
            if exc.name == "huey":
                raise SystemExit(
                    "queue support is optional; install it with `uv sync --extra queue`"
                ) from exc
            raise
        plan = prepare_queued_run(config, search_root=search_root)
        job_ids = unfinished_job_ids(plan)
        # This command is the explicit recovery/reconciliation boundary: stale
        # unfinished application states are reset before fresh broker messages
        # are submitted. Durable attempt budgets are preserved.
        mark_enqueued(plan, job_ids)
        for job_id in job_ids:
            enqueue_payload(task_payload(plan, job_id))
        print(
            json.dumps(
                {
                    "run_id": plan.run_id,
                    "enqueued": len(job_ids),
                    "output_directory": str(plan.config.output.directory),
                },
                indent=2,
            )
        )
    elif args.command == "worker":
        try:
            workers = resolve_worker_count(args.workers)
        except ValueError as exc:
            parser.error(str(exc))
        try:
            from huey.consumer import Consumer
            from .queue_huey import huey
            from .queueing import consumer_process_lock
        except ModuleNotFoundError as exc:
            if exc.name == "huey":
                raise SystemExit(
                    "queue support is optional; install it with `uv sync --extra queue`"
                ) from exc
            raise
        try:
            with consumer_process_lock():
                Consumer(
                    huey, workers=workers, worker_type="thread", periodic=False
                ).run()
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
    elif args.command == "queue-status":
        from .queueing import queue_status

        print(json.dumps(queue_status(args.run), indent=2))
    elif args.command == "finalize":
        from .queueing import finalize_queued_run

        manifest = finalize_queued_run(args.run)
        print(json.dumps(manifest.model_dump(mode="json"), indent=2))
    elif args.command == "validate-config":
        recipes = validate_config_semantics(config, search_root=search_root)
        print(
            json.dumps(
                {
                    "valid": True,
                    "name": config.name,
                    "config_hash": config_hash(config, recipes),
                    "endpoint_profile": config.endpoint.profile,
                    "backend": config.endpoint.backend,
                    "model": config.endpoint.model,
                    "recipe_count": len(recipes),
                    "output_directory": str(config.output.directory),
                },
                indent=2,
            )
        )
    elif args.command == "inspect":
        print(json.dumps(inspect_run(config, search_root=search_root), indent=2))
    elif args.command == "list-formats":
        print(
            json.dumps(
                [
                    {
                        "name": item.name,
                        "display_name": item.display_name,
                        "implemented": item.implemented,
                        "prompt_version": item.prompt_version,
                    }
                    for item in list_formats()
                ],
                indent=2,
            )
        )
    elif args.command in {"list-languages", "list-source-languages"}:
        print(json.dumps([item.name for item in list_languages()], indent=2))
    elif args.command == "list-chunkers":
        print(json.dumps(list_chunkers(), indent=2))
    elif args.command == "list-prompt-packs":
        print(json.dumps(list_prompt_packs(), indent=2))
    elif args.command == "list-endpoints":
        print(json.dumps(ENDPOINT_PROFILES, indent=2))
    elif args.command == "export":
        if args.format == "parquet":
            export_parquet(args.dataset, args.output)
        elif args.format == "csv":
            export_csv(args.dataset, args.output)
        else:
            export_legacy_jsonl(args.dataset, args.output)
        print(json.dumps({"output": str(args.output), "format": args.format}, indent=2))


def _legacy_main(arguments: list[str]) -> None:
    args = build_legacy_parser().parse_args(arguments)
    config = AppConfig(
        input_path=args.input,
        output_path=args.output,
        source_id=args.source_id,
        source_language=args.source_language,
        chunk_size=args.chunk_size,
        resume=args.resume,
        backend=BackendConfig(
            name=args.backend_name,
            base_url=args.backend_base_url,
            model=args.backend_model,
            api_key=args.backend_api_key,
        ),
    )
    from .pipeline import run_pipeline

    rows = run_pipeline(config)
    print(f"generated {len(rows)} validated rows -> {config.output_path}")


def resolve_worker_count(cli_value: int | None, environ: dict[str, str] | None = None) -> int:
    environment = os.environ if environ is None else environ
    raw = cli_value if cli_value is not None else environment.get("LLM_DATA_GEN_WORKERS", "1")
    try:
        workers = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("worker count must be a positive integer") from exc
    if workers < 1:
        raise ValueError("worker count must be at least 1")
    return workers
