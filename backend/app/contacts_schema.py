"""Explicit provisioning for the owner-private contact index."""
from .models import Contact, ContactDreamCursor


def upgrade_contacts_schema(engine):
    for model in (Contact, ContactDreamCursor):
        model.__table__.create(engine, checkfirst=True)
    from .contacts_dream import seed_contact_dreams
    with engine.begin() as connection:
        seed_contact_dreams(connection)


def check_contacts_schema(inspector):
    for model in (Contact, ContactDreamCursor):
        table = model.__table__
        if not inspector.has_table(table.name) or not set(table.columns.keys()).issubset(
                {column["name"] for column in inspector.get_columns(table.name)}):
            raise RuntimeError("Run python -m app.init_db before starting API or worker")
