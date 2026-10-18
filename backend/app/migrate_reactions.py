"""Provision only message reactions; never seed or invoke a model."""
from .db import engine
from .reaction_schema import upgrade_reaction_schema


def main():
    upgrade_reaction_schema(engine)
    print("Message reaction schema is ready.")


if __name__ == "__main__":
    main()
