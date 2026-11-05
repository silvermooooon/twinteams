"""Synthetic dependent history shared by isolated SQL and browser measurements."""
from sqlalchemy import func, insert, select, text

from app.models import Chat, Message, Receipt, ReplyRequest
from app.service import digest


def seed_chat_history(service, count=5000):
    with service.transaction() as db:
        if not db.scalar(text("SELECT current_schema()")).startswith("tt_test_"):
            raise RuntimeError("Performance history requires a disposable test schema")
        chat = db.get(Chat, "private-alex")
        first = chat.next_message_seq
        accepted = db.scalar(select(func.max(ReplyRequest.accepted_seq)).where(
            ReplyRequest.chat_id == chat.id, ReplyRequest.target_twin_id == "twin-alex")) or 0
        rows, requests, receipts, sources = [], [], [], []
        for offset in range(count):
            seq = first + offset
            identifier = f"performance-message-{seq}"
            body = f"Synthetic conversation entry {seq}: delivery details and next steps."
            twin = offset % 2 == 1
            request_id = f"performance-request-{seq}" if twin else None
            dependencies = sources[-80:] if twin else []
            rows.append({"id": identifier, "chat_id": chat.id, "seq": seq,
                "kind": "twin" if twin else "human", "author_id": "twin-alex" if twin else "owner-alex",
                "body": body, "request_id": request_id, "source_message_ids": [item["id"] for item in dependencies],
                "source_provenance": dependencies, "source_floor_seq": first})
            sources.append({"kind": "message", "id": identifier, "seq": seq, "hash_version": 1,
                "hash": digest([body, None, []])})
            if twin:
                accepted += 1
                requests.append({"id": request_id, "chat_id": chat.id, "target_twin_id": "twin-alex",
                    "requester_id": "owner-alex", "source": "private", "status": "completed",
                    "source_message_id": rows[-2]["id"], "accepted_seq": accepted,
                    "intent_key": request_id, "client_request_key": request_id, "private_generation": chat.reset_generation,
                    "execution_config": {"fixture": "Frozen execution configuration " * 200}})
                receipts.append({"message_id": identifier, "owner_id": "owner-alex"})
        for start in range(0, len(rows), 250):
            db.execute(insert(Message), rows[start:start + 250])
        for model, values in ((ReplyRequest, requests), (Receipt, receipts)):
            for start in range(0, len(values), 250):
                db.execute(insert(model), values[start:start + 250])
        chat.next_message_seq = first + count
        other = db.get(Chat, "group-alex-maya")
        other_seq = other.next_message_seq
        db.add(Message(id="performance-peer-message", chat_id=other.id, seq=other_seq,
            author_id="owner-maya", kind="human", body="Synthetic peer conversation latest message."))
        other.next_message_seq += 1
        return {"test_only_canonical_history_seed": True, "model_runs_added": 0,
            "private_chat_id": chat.id, "private_chat_title": service.chat_title(db, chat, "owner-alex"),
            "peer_chat_id": other.id, "other_chat_title": service.chat_title(db, other, "owner-alex"),
            "latest_message_id": rows[-1]["id"], "latest_message_body": rows[-1]["body"],
            "peer_latest_message_id": "performance-peer-message", "history_count": count,
            "completed_requests": len(requests), "read_receipts": len(receipts), "first_seq": first}
