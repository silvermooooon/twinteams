"""Explicit single-tenant account provisioning for a verified identity subject."""
import argparse
from .models import Actor, Chat, Membership, Owner, uid
from .service import service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--owner-id")
    args = parser.parse_args()
    service.initialize()
    owner_id = args.owner_id or uid()
    with service.transaction() as db:
        if db.get(Owner, owner_id):
            raise SystemExit("Owner ID already exists; account unchanged")
        db.add(Owner(id=owner_id, auth_subject=args.subject))
        db.flush()
        initials = "".join(part[0] for part in args.name.split()[:2]).upper()
        db.add_all([Actor(id=owner_id, owner_id=owner_id, kind="human", name=args.name, initials=initials, color="#7962bd"), Actor(id=uid(), owner_id=owner_id, kind="twin", name=f"{args.name}'s Twin", initials=f"{initials[:1]}T", color="#6dbdad")])
        db.flush()
        chat = Chat(kind="private", title=f"{args.name}'s Twin", private_owner_id=owner_id)
        db.add(chat)
        db.flush()
        db.add(Membership(chat_id=chat.id, owner_id=owner_id, history_policy="all", lower_seq=1))
    print(f"Provisioned owner {owner_id}; the verified token subject must match the configured issuer/audience/tenant.")


if __name__ == "__main__":
    main()
