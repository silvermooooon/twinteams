"""Previously accepted task fixtures survive the owner-only manual-entry boundary."""


def legacy_request(product, requester, chat_id, message_id, twin_id, key):
    # Exercise existing immutable task/runtime contracts without treating the
    # retired cross-owner HTTP/WS manual entry point as still authorized.
    with product.transaction() as db:
        chat, _ = product.membership(db, requester, chat_id, True)
        request = product.accept_request(db, chat, twin_id, requester, "manual", key,
                                         source_message_id=message_id)
        return product.request_json(db, request, requester)
