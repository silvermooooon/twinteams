"""Explicit additive provisioning, never an API startup side effect."""
from .question_models import QuestionRequest


def upgrade_question_schema(engine):
    QuestionRequest.__table__.create(engine, checkfirst=True)
    for index in QuestionRequest.__table__.indexes:
        index.create(engine, checkfirst=True)


def check_question_schema(inspector):
    table = QuestionRequest.__table__
    if (not inspector.has_table(table.name) or not set(table.columns.keys()).issubset(
            {column["name"] for column in inspector.get_columns(table.name)})
            or not {index.name for index in table.indexes}.issubset(
                {index["name"] for index in inspector.get_indexes(table.name)})):
        raise RuntimeError("Run python -m app.init_db before starting API or worker")
