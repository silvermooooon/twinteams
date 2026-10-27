"""SDK identity belongs to a task, independently of its displayed chat."""
from uuid import NAMESPACE_URL, uuid5


def legacy_session_id(chat_id, twin_id, private_generation=None):
    identity = f"twinteams:chat:{chat_id}:twin:{twin_id}"
    if private_generation:
        identity += f":private-generation:{private_generation}"
    return uuid5(NAMESPACE_URL, identity).hex


def request_session_id(request_id):
    return uuid5(NAMESPACE_URL, f"twinteams:request:{request_id}").hex
