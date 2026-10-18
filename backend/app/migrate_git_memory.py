"""Explicit owner-scoped legacy import. Originals remain untouched after migration."""
import argparse
import asyncio

from sqlalchemy import select

from .git_memory import migrate_store
from .models import Owner
from .personal import own_memory_store
from .service import service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id")
    args = parser.parse_args()
    with service.transaction() as db:
        owners = [args.owner_id] if args.owner_id else list(db.scalars(select(Owner.id)))
    for owner_id in owners:
        store = own_memory_store(owner_id)
        if store is None:
            raise RuntimeError("Memory is disabled")
        result = asyncio.run(migrate_store(store))
        print(owner_id, result["status"], result["imported"])


if __name__ == "__main__":
    main()
