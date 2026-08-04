"""CLI entry point.

    python -m scraper                  # full pipeline run
    python -m scraper --no-analyze     # ingest + dedup only (seed the knowledge base)
    python -m scraper --no-discovery   # skip the web-search discovery layer
    python -m scraper check-sources    # verify every configured source works
    python -m scraper site             # regenerate site/ from the knowledge base
"""
import argparse
import logging
import os
from pathlib import Path

from scraper.pipeline import check_sources, run


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from .env without overriding real env vars, so
    `python -m scraper` behaves identically to the double-click wrappers.
    Lines starting with # and lines without '=' are ignored."""
    env_file = Path(path)
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()


def main() -> int:
    parser = argparse.ArgumentParser(prog="scraper")
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "check-sources", "site", "backfill"])
    parser.add_argument("--days", type=int, default=45,
                        help="backfill window in days (backfill command only)")
    parser.add_argument("--no-analyze", action="store_true",
                        help="ingest and update the knowledge base without calling the LLM")
    parser.add_argument("--no-discovery", action="store_true",
                        help="skip web-search-based source discovery")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    load_dotenv()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # requests/urllib3 are noisy at INFO
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    if args.command == "check-sources":
        return 1 if check_sources() else 0

    if args.command == "backfill":
        from scraper.pipeline import backfill
        backfill(days=args.days)
        return 0

    if args.command == "site":
        from scraper.export.site import export_site
        from scraper.store import Store
        index = export_site(Store())
        print(f"Site regenerated — open {index}")
        return 0

    run(skip_analysis=args.no_analyze, skip_discovery=args.no_discovery)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
