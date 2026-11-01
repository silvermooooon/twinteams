"""Authenticated APIs for the current owner's private contacts."""
from fastapi import APIRouter, Query
from pydantic import Field

from . import contacts
from .api import Input, OwnerIdentity
from .service import service

router = APIRouter(prefix="/api/me/twin/contacts")


class ContactTags(Input):
    tags: list[str] = Field(max_length=8)
    expected_revision: int | None = Field(None, ge=1)


@router.get("")
def list_contacts(owner: OwnerIdentity, q: str = Query("", max_length=256),
        limit: int = Query(100, ge=1, le=100)):
    return contacts.list_contacts(service, owner, q, limit)


@router.get("/{contact_owner_id}")
def get_contact(contact_owner_id: str, owner: OwnerIdentity):
    return contacts.get_contact(service, owner, contact_owner_id)


@router.put("/{contact_owner_id}/tags")
def edit_tags(contact_owner_id: str, data: ContactTags, owner: OwnerIdentity):
    return contacts.edit_tags(service, owner, contact_owner_id, data.tags, data.expected_revision)
