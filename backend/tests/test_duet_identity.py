"""Display-name upgrades preserve customized identity and canonical history."""
from app import duet_identity, soul_file
from app.models import Actor, Chat, Message
from test_memory_dream import product as product


def test_default_names_upgrade_is_repeatable_and_keeps_custom_names_and_soul(product, monkeypatch, tmp_path):
    from dataclasses import replace
    monkeypatch.setattr(duet_identity, "settings", replace(duet_identity.settings, workspace_root=tmp_path))
    with product.transaction() as db:
        actor = db.get(Actor, "twin-alex")
        actor.name = "AAA's Twin"
        db.get(Chat, "private-alex").title = "AAA's Twin"
        custom = db.get(Actor, "twin-maya")
        custom.name = "Javis"
        db.get(Chat, "private-maya").title = "BBB's Twin"
        # A custom name mentioning the old concept is still a custom name.
        db.get(Actor, "twin-jordan").name = "Twin Peaks"
        historical = product.append_message(db, db.get(Chat, "private-alex"),
            author_id="twin-alex", kind="twin", body="An original Twin-era message")
        historical_id = historical.id
        revision = actor.profile_revision
    workspace = tmp_path / "private-alex"
    soul_file.initialize(workspace, "AAA's Twin")
    body = "Keep this custom persona, even its reference to Twin Peaks.\n"
    soul_file.atomic_write(workspace / "SOUL.md", soul_file.heading("AAA's Twin") + "\n\n" + body)
    deleted = tmp_path / "private-jordan"
    soul_file.initialize(deleted, "Twin Peaks")
    (deleted / "SOUL.md").unlink()

    assert duet_identity.upgrade_default_names(product) == 1
    assert duet_identity.upgrade_default_names(product) == 0
    with product.transaction() as db:
        actor = db.get(Actor, "twin-alex")
        assert actor.id == "twin-alex" and actor.kind == "twin"
        assert actor.name == "AAA's Duet" and actor.profile_revision == revision + 1
        assert db.get(Chat, "private-alex").title == "AAA's Duet"
        assert db.get(Actor, "twin-maya").name == "Javis"
        assert db.get(Chat, "private-maya").title == "Javis"
        assert db.get(Actor, "twin-jordan").name == "Twin Peaks"
        assert db.get(Message, historical_id).body == "An original Twin-era message"
    assert soul_file.read(workspace)["content"] == soul_file.heading("AAA's Duet") + "\n\n" + body
    assert not (deleted / "SOUL.md").exists()
