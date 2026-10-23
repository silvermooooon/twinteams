"""Additive, repeatable owner onboarding progress; reset does not delete it."""
from .models import OnboardingState


def upgrade_onboarding_schema(engine):
    OnboardingState.__table__.create(engine, checkfirst=True)


def check_onboarding_schema(inspector):
    table = OnboardingState.__table__
    if not inspector.has_table(table.name) or not set(table.columns.keys()).issubset(
            {column["name"] for column in inspector.get_columns(table.name)}):
        raise RuntimeError("Run python -m app.init_db before starting API or worker")
