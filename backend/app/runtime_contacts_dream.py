"""App Dream composition: SDK memory draft plus staged contact tag proposals."""
from agent_runtime import Agent
from agent_runtime.memory.dreaming import PROMPT
from agent_runtime.memory.git import MemoryPublishUnknown
from agent_runtime.memory.store import MemoryConflict, MemorySource
from agent_runtime.memory.tools import create_memory_tools, result
from agent_runtime.sessions import ToolRecoveryRequired
from agent_runtime.types import AgentTool

from .contacts import validate_tags


async def run_contacts_dream(*, session, store, experiences, sources, source_reader, contacts, private_sources, **options):
    store.check_identity(session.info)
    if session.snapshot.get("run_id") is not None or session.context_entries():
        raise ValueError("Dream attempts require a fresh Session")
    candidates = {item["owner_id"]: item for item in contacts}
    async with store.draft() as draft:
        tools = create_memory_tools(session=session, store=draft, sources=tuple(private_sources), read_only=not private_sources)

        async def read_source(call_id, args, signal=None, on_update=None):
            return result({"source": args, "content": await source_reader(MemorySource(args["session_id"], args["event_id"]))})

        async def stage_tags(call_id, args, signal=None, on_update=None):
            candidate = candidates.get(args["owner_id"])
            tags = validate_tags(args["tags"])
            refs = args["source_message_ids"]
            if not candidate or not refs or not set(refs).issubset(candidate["source_message_ids"]):
                raise ValueError("Contact tags require this contact's authorized fresh human evidence")
            return result({"contact_proposal": {"owner_id": args["owner_id"], "tags": tags,
                "source_message_ids": list(dict.fromkeys(refs))}})

        async def publish(call_id, args, signal=None, on_update=None):
            try:
                version = await draft.publish(session.tool_operation_id(call_id))
            except MemoryPublishUnknown:
                raise ToolRecoveryRequired([call_id]) from None
            except MemoryConflict as conflict:
                return result(conflict.to_dict(), error=True, terminate=True)
            return result({"status": "published", "version": version}, terminate=True)

        tools.extend([
            AgentTool("read_source", "Read authorized conversation evidence.", {"type": "object",
                "properties": {"session_id": {"type": "string"}, "event_id": {"type": "string"}},
                "required": ["session_id", "event_id"], "additionalProperties": False}, read_source, replay="safe", version="memory-1"),
            AgentTool("stage_contact_tags", "Stage brief project + responsibility tags for one existing human contact, with supporting message IDs. Applied only after Dream publication.",
                {"type": "object", "properties": {"owner_id": {"type": "string"},
                    "tags": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 64}},
                    "source_message_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}}},
                    "required": ["owner_id", "tags", "source_message_ids"], "additionalProperties": False},
                stage_tags, replay="safe", version="contacts-1"),
            AgentTool("memory_publish", "Publish staged memory and finish this Dream, including contact tag proposals.",
                {"type": "object", "properties": {}, "additionalProperties": False}, publish,
                replay="never", execution_mode="sequential", version="memory-1"),
        ])
        prompt = PROMPT + """
Writes and contact proposals are staged. Finish with memory_publish even if only contacts change.
Human interaction evidence marked contact_tags_only is solely for contact tags; NEVER write it
to private Memory. Only authorized private conversation evidence can justify Memory edits.
For supplied human contacts, maintain a few short tags that associate a named project with the
person's explicitly supported role or responsibility, e.g. 'Atlas · frontend'. Never infer a role
from group membership, a single task assignment, your own claims or private owner conversations.
Use only each contact's supplied fresh human source_message_ids to stage_contact_tags. Preserve
existing tags unless fresh explicit evidence corrects them. Keep people as contacts, not projects.
Contact tags are secondary navigation hints; do not build profiles or copy conversation text.
"""
        agent = Agent(session=session, tools=tools, system_prompt=prompt, **options)
        await agent.prompt(experiences)
        if session.snapshot["status"] != "completed" or draft.published_version is None:
            raise RuntimeError("Dream was not published; keep processing cursors unchanged")


def contact_proposals(records):
    """Durable successful tool results also survive a lost publication acknowledgement."""
    proposals = {}
    for row in records:
        if row["type"] not in {"tool_returned", "tool_completed"}:
            continue
        result = row.get("data", {}).get("result", {})
        if not result.get("isError"):
            proposal = result.get("details", {}).get("memory", {}).get("contact_proposal")
            if proposal:
                proposals[proposal["owner_id"]] = proposal
    return list(proposals.values())
