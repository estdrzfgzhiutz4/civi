#!/usr/bin/env python3
# -------------------------------------------------------------
# archive_model.py  •  CivitAI‑Model‑Archiver
#
# Download any combination of CivitAI models, LoRAs and embeddings
# specified as usernames, numeric IDs or full CivitAI URLs.  Input can
# come from command‑line flags, a text file, or standard input.
# -------------------------------------------------------------
from __future__ import annotations

import argparse
import logging
import re
import sys
import urllib.parse
from pathlib import Path

from core.metadata_extractor import MetadataExtractor
from core.task_builder import TaskBuilder

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #
ID_REGEX = re.compile(r"/models/(\d+)", re.IGNORECASE)


def extract_model_id(value: str) -> str | None:
    """Return the numeric model ID from a raw ID or any CivitAI URL."""
    value = value.strip()
    if not value:
        return None

    # Plain number?
    if value.isdigit():
        return value

    # Otherwise treat as URL
    try:
        parsed = urllib.parse.urlparse(value)
    except ValueError:
        return None

    match = ID_REGEX.search(parsed.path)
    return match.group(1) if match else None


def dedupe_keep_order(seq):
    """Yield items once, preserving the original order."""
    seen: set[str] = set()
    for item in seq:
        if item not in seen:
            seen.add(item)
            yield item


# ------------------------------------------------------------------ #
# CLI
# ------------------------------------------------------------------ #

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Archive CivitAI models / LoRAs / embeddings.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Destination & authentication
    p.add_argument("--output-dir", default="model_archives", help="Destination folder")
    p.add_argument(
        "--token",
        default="",
        help="Optional CivitAI API token for higher rate‑limits / private models",
    )

    # Retry behaviour
    p.add_argument("--retry-delay", type=int, default=20, help="Seconds between retries")
    p.add_argument("--max-tries", type=int, default=5, help="Attempts before giving up")

    # Model selection
    p.add_argument(
        "--usernames",
        nargs="*",
        help="CivitAI usernames whose *entire* portfolio should be archived.",
    )
    p.add_argument(
        "--models",
        nargs="*",
        metavar="ID_OR_URL",
        help="One or more model IDs **or** raw CivitAI URLs.",
    )
    p.add_argument(
        "--models-file",
        help="Path to a text file containing one ID/URL per line (alternative to --models)",
    )

    # Filters supported by TaskBuilder
    p.add_argument(
        "--only-base-models",
        nargs="*",
        default=None,
        help="Restrict to models whose base matches one of these (e.g. SDXL).",
    )
    p.add_argument(
        "--only-model-file-types",
        nargs="*",
        default=None,
        help="Download only the given file‑extensions (e.g. safetensors ckpt).",
    )
    p.add_argument(
        "--skip-compress-models",
        action="store_true",
        help="Do **not** 7‑zip .safetensors after verification.",
    )
    return p


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

def main() -> None:
    args = build_arg_parser().parse_args()

    # Standard logging setup
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logger = logging.getLogger("archive_model")

    # -------------------------------------------------------------- #
    # Gather model inputs from CLI, file, or STDIN
    # -------------------------------------------------------------- #
    model_inputs: list[str] = []

    if args.models:
        model_inputs.extend(args.models)

    if args.models_file:
        try:
            with open(args.models_file, "r", encoding="utf-8") as fh:
                model_inputs.extend(line.strip() for line in fh if line.strip())
        except OSError as exc:
            logger.error("Cannot read --models-file %s: %s", args.models_file, exc)
            sys.exit(1)

    # If no explicit models supplied but data is piped in, read from stdin
    if not model_inputs and not sys.stdin.isatty():
        model_inputs.extend(line.strip() for line in sys.stdin if line.strip())

    model_ids: list[str] = []
    for item in model_inputs:
        mid = extract_model_id(item)
        if mid:
            model_ids.append(mid)
        else:
            logger.warning('Ignoring invalid model specifier: "%s"', item)

    model_ids = list(dedupe_keep_order(model_ids))

    if not model_ids and not args.usernames:
        logger.error("Nothing to do: no model IDs / URLs or usernames supplied.")
        sys.exit(1)

    logger.info("Models resolved: %s", ", ".join(model_ids) or "— (usernames only)")

    # -------------------------------------------------------------- #
    # Extract metadata & build download tasks
    # -------------------------------------------------------------- #
    extractor = MetadataExtractor(
        token=args.token, max_tries=args.max_tries, retry_delay=args.retry_delay
    )
    models = extractor.extract(usernames=args.usernames, model_ids=model_ids)

    if not models:
        logger.error("No models found; exiting.")
        sys.exit(1)

    task_builder = TaskBuilder(
        output_dir=args.output_dir,  # str expected by TaskBuilder/Tools
        token=args.token,
        max_tries=args.max_tries,
        retry_delay=args.retry_delay,
        only_base_models=args.only_base_models,
        only_model_file_types=args.only_model_file_types,
        skip_compress_models=args.skip_compress_models,
    )

    tasks = task_builder.build_tasks(models)

    # -------------------------------------------------------------- #
    # Execute tasks
    # -------------------------------------------------------------- #
    failures = 0
    for task in tasks:
        if not task.run():
            failures += 1

    if failures:
        logger.warning("Completed with %d failed task(s).", failures)
        sys.exit(failures)
    else:
        logger.info("All tasks completed successfully.")
        sys.exit(0)


if __name__ == "__main__":
    main()
