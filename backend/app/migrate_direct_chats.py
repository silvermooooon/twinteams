"""Explicit pair-key-only provisioning. Never merges or deletes existing chats."""
from .db import engine
from .direct_chat_schema import upgrade_direct_chat_schema


def main():
    upgrade_direct_chat_schema(engine)
    print("Direct chat pair keys and unique index are ready.")


if __name__ == "__main__":
    main()
