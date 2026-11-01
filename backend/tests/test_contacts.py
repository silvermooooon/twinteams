"""Private contacts follow real, visible human interactions in isolated PostgreSQL."""
import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_memory_dream import product as product  # noqa: PLC0414

from app import contact_api, contacts
from app.models import Actor, Chat, Contact, Membership, Message


def publish(product, *, chat="group-team", author="owner-alex", reply=None, kind="human",
        body="I own Atlas settlement development"):
    with product.transaction() as db:
        return product.append_message(db, db.get(Chat, chat), author_id=author, kind=kind,
            body=body, reply_to_message_id=reply).id


def test_contacts_require_interaction_and_respect_each_owners_visibility(product):
    assert contacts.list_contacts(product, "owner-alex")["items"] == []
    general = publish(product, author="owner-maya")
    assert contacts.list_contacts(product, "owner-alex")["items"] == []
    publish(product, reply=general)
    alex = contacts.list_contacts(product, "owner-alex")["items"]
    assert [row["owner_id"] for row in alex] == ["owner-maya"]
    assert alex[0]["tags"] == []
    assert [row["owner_id"] for row in contacts.list_contacts(product, "owner-maya")["items"]] == ["owner-alex"]
    assert contacts.list_contacts(product, "owner-jordan")["items"] == []

    with product.transaction() as db:
        hidden = db.get(Message, general)
        db.scalar(select(Membership).where(
            Membership.chat_id == "group-team", Membership.owner_id == "owner-jordan")).lower_seq = hidden.seq + 1
    publish(product, author="owner-jordan", reply=general)
    assert contacts.list_contacts(product, "owner-jordan")["items"] == []
    assert [row["owner_id"] for row in contacts.list_contacts(product, "owner-maya")["items"]] == ["owner-jordan", "owner-alex"]


def test_direct_contact_empty_tags_search_manual_clear_and_revision(product):
    publish(product, chat="group-alex-maya", author="owner-maya")
    assert contacts.list_contacts(product, "owner-alex", "BBB")["items"][0]["tags"] == []
    tagged = contacts.edit_tags(product, "owner-alex", "owner-maya", [" Atlas · Settlement Dev "])
    assert tagged["tags"] == ["Atlas · Settlement Dev"]
    assert tagged["manual_revision"] == 1 and tagged["manual_updated_at"]
    assert contacts.list_contacts(product, "owner-alex", "settlement")["items"]
    assert contacts.list_contacts(product, "owner-alex", "Atlas Settlement")["items"][0]["owner_id"] == "owner-maya"
    assert contacts.list_contacts(product, "owner-alex", "BBB settlement")["items"]
    assert not contacts.list_contacts(product, "owner-alex", "Atlas unknown")["items"]
    assert not contacts.list_contacts(product, "owner-maya", "settlement")["items"]
    with pytest.raises(HTTPException) as error:
        contacts.edit_tags(product, "owner-alex", "owner-maya", ["Stale"], expected_revision=1)
    assert error.value.status_code == 409
    assert contacts.edit_tags(product, "owner-alex", "owner-maya", [], tagged["revision"])["tags"] == []
    with pytest.raises(HTTPException) as error:
        contacts.edit_tags(product, "owner-alex", "owner-jordan", ["Unknown"])
    assert error.value.status_code == 404
    for invalid in (["x" * 65], [""], ["one"] * 9):
        with pytest.raises(HTTPException):
            contacts.edit_tags(product, "owner-alex", "owner-maya", invalid)


def test_group_mentions_require_one_identifiable_current_human_member(product):
    with product.transaction() as db:
        db.get(Actor, "owner-maya").name = "Same"
        db.get(Actor, "owner-jordan").name = "Same"
    publish(product, body="@Same @twin-maya @owner-missing please help")
    assert contacts.list_contacts(product, "owner-alex")["items"] == []
    publish(product, body="@owner-maya, please check this.")
    assert [row["owner_id"] for row in contacts.list_contacts(product, "owner-alex")["items"]] == ["owner-maya"]
    assert [row["owner_id"] for row in contacts.list_contacts(product, "owner-maya")["items"]] == ["owner-alex"]
    assert contacts.list_contacts(product, "owner-jordan")["items"] == []
    with product.transaction() as db:
        db.get(Actor, "owner-jordan").name = "CCC"
    publish(product, body="Thanks @CCC.")
    assert {row["owner_id"] for row in contacts.list_contacts(product, "owner-alex")["items"]} == {"owner-maya", "owner-jordan"}


def test_owner_api_and_contact_tools_keep_directory_profiles_public(product, monkeypatch):
    from app.main import app
    monkeypatch.setattr(contact_api, "service", product)
    publish(product, chat="group-alex-maya", author="owner-maya")
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"username": "DEV-AAA"}).status_code == 200
    assert client.get("/api/me/twin/contacts/owner-jordan").json() == {"contact": None}
    result = client.put("/api/me/twin/contacts/owner-maya/tags",
        json={"tags": ["Atlas · Settlement Dev"]})
    assert result.status_code == 200
    assert client.get("/api/me/twin/contacts?q=Settlement").json()["items"]
    assert client.post("/api/auth/login", json={"username": "DEV-BBB"}).status_code == 200
    assert client.get("/api/me/twin/contacts?q=Settlement").json()["items"] == []
    request = product.send_message("owner-alex", "private-alex", "Find a contact", uuid4().hex)["request"]
    context = product.get_runtime_context(request["id"])
    def tool(name, arguments, **extra):
        return asyncio.run(product.runtime_tool(name, arguments, {**context, "operation_id": uuid4().hex, **extra}))
    found = tool("search_contacts", {"query": "Settlement"})["items"][0]
    assert found["owner_id"] == "owner-maya"
    by_id = tool("list_people", {"query": "owner-maya"})["people"]
    assert by_id[0]["owner_id"] == "owner-maya" and "tags" not in by_id[0]
    changed = tool("update_contact_tags", {"contact_owner_id": "owner-maya", "tags": [],
        "expected_revision": found["revision"]})
    assert changed["tags"] == []
    with product.transaction() as db:
        assert db.get(Contact, ("owner-maya", "owner-alex")).tags == []
