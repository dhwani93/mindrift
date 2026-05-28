"""Mindrift — Daily Video Pipeline.

Usage:
    python main.py                    # Full pipeline with Telegram approval
    python main.py --dry-run          # Generate but don't upload
    python main.py --date 2026-05-27  # Specific date
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

env_path = Path(__file__).parent / "config" / ".env"
load_dotenv(env_path)


def setup_logging(run_date: str) -> None:
    log_dir = Path(__file__).parent / "output" / run_date
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_dir / "pipeline.log"),
        ],
    )


def main():
    parser = argparse.ArgumentParser(description="Mindrift — Daily Pipeline")
    parser.add_argument("--date", type=str, default=date.today().isoformat())
    parser.add_argument("--dry-run", action="store_true", help="Generate but don't upload")
    args = parser.parse_args()

    setup_logging(args.date)
    logger = logging.getLogger("main")
    logger.info(f"Mindrift Pipeline — {args.date}")

    from agents.orchestrator import Orchestrator

    orchestrator = Orchestrator()
    summary = orchestrator.run_daily(run_date=args.date, dry_run=args.dry_run)

    if summary["status"] == "success":
        logger.info("Pipeline completed successfully!")
        sys.exit(0)
    else:
        logger.error(f"Pipeline failed: {summary.get('error', 'unknown')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
