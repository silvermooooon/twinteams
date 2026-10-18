"""Viewer-specific peer names and member-authorized group names; no model calls."""
from uuid import uuid4

from fastapi.testclient import TestClient
from test_memory_dream import product as product  # noqa: PLC0414

from app.main import app
from app.models import Actor, Chat


def client(name):
    result = TestClient(app)
    assert result.post("/api/auth/login", json={"username": name}).status_code == 200
    return result


def test_pair_titles_are_peer_human_names_across_bootstrap_detail_summary_and_tools(product):
    alex, maya = client("DEV-AAA"), client("DEV-BBB")
    created = alex.post("/api/chats", json={"owner_ids": ["owner-maya"], "title": "Ignored pair title"})
    assert created.status_code == 200
    pair = created.json()
    assert pair["kind"] == "group" and pair["title"] == "BBB" and not pair["can_rename"]
    for current, expected in ((alex, "BBB"), (maya, "AAA")):
        bootstrap = next(row for row in current.get("/api/bootstrap").json()["chats"] if row["id"] == pair["id"])
        detail = current.get(f"/api/chats/{pair['id']}").json()["chat"]
        summary = current.get(f"/api/chats/{pair['id']}/summary").json()
        assert bootstrap["title"] == detail["title"] == summary["title"] == expected
        assert current.patch(f"/api/chats/{pair['id']}", json={"title": "Cannot rename peer"}).status_code == 409
    origin = product.send_message("owner-alex", "private-alex", "List my chats", uuid4().hex)["request"]
    context = product.get_runtime_context(origin["id"])
    choices = product._runtime_tool_transaction("list_authorized_chats", {}, context)["chats"]
    assert next(row for row in choices if row["id"] == pair["id"])["title"] == "BBB"
    with product.transaction() as db:
        request = product.accept_request(db, db.get(Chat, pair["id"]), "twin-maya", "owner-alex", "manual", uuid4().hex, raw="Context")
        request_id = request.id
        peer = db.get(Actor, "owner-alex")
        peer.name = "Changed human name"
    assert product.get_runtime_context(request_id)["chat"]["title"] == "Changed human name"
    assert maya.get(f"/api/chats/{pair['id']}/summary").json()["title"] == "Changed human name"
    assert alex.patch("/api/chats/private-alex", json={"title": "Wrong kind"}).status_code == 409


def test_group_rename_trim_permissions_revision_validation_and_seed_preservation(product):
    alex, maya, jordan = client("DEV-AAA"), client("DEV-BBB"), client("DEV-CCC")
    pair = alex.post("/api/chats", json={"owner_ids": ["owner-maya"]}).json()
    assert jordan.patch(f"/api/chats/{pair['id']}", json={"title": "Unauthorized"}).status_code == 403
    added = alex.post(f"/api/chats/{pair['id']}/members", json={"owner_ids": ["owner-jordan"], "history_policy": "all"}).json()
    assert added["can_rename"] and added["title"] == "AAA, BBB, CCC"
    before = maya.get(f"/api/chats/{pair['id']}/summary").json()["revision"]
    renamed = maya.patch(f"/api/chats/{pair['id']}", json={"title": "  Research room  "})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Research room"
    assert renamed.json()["revision"] > before
    assert jordan.get(f"/api/chats/{pair['id']}/summary").json()["title"] == "Research room"
    assert maya.patch(f"/api/chats/{pair['id']}", json={"title": "  "}).status_code == 400
    assert maya.patch(f"/api/chats/{pair['id']}", json={"title": "x" * 257}).status_code == 400
    assert maya.patch(f"/api/chats/{pair['id']}", json={"title": "ok", "owner_id": "owner-alex"}).status_code == 422
    assert maya.patch(f"/api/chats/{pair['id']}", json={"title": None}).status_code == 422
    # Existing seeded pair can become a real group; startup never restores its old placeholder.
    product.add_members("owner-alex", "group-alex-maya", ["owner-jordan"], "all")
    product.rename_chat("owner-alex", "group-alex-maya", "Keep chosen group name")
    product.seed_local()
    assert alex.get("/api/chats/group-alex-maya/summary").json()["title"] == "Keep chosen group name"
    assert alex.post(f"/api/chats/{pair['id']}/members", json={"owner_ids": ["owner-jordan"], "history_policy": "all"}).json()["title"] == "Keep chosen group name"
