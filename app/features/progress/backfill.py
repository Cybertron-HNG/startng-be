"""Run once after the Progress Engine ships, to give existing players runs from their saved scores:

    python -m app.features.progress.backfill

Safe to run again; it only adds what is missing. (See service.backfill_from_scores.)
"""
from ...db import SessionLocal
from . import service


def main() -> int:
    with SessionLocal() as db:
        made = service.backfill_from_scores(db)
    print(f"Made {made} run{'' if made == 1 else 's'} from saved scores.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
