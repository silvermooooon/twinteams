"""Bounded, manually reviewed Duet social acceptance using the real Responses SDK.

Default prints the synthetic plan. --live reuses the explicitly authorized local worker
provider. Production prompts and tool declarations are loaded at execution time; only
executors are replaced. No production service, database, messages, or workers are used.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace


def message(id, owner, body, *, duet=False):
    return {"id": id, "author_id": owner.replace("owner-", "twin-") if duet else owner,
            "author_owner_id": owner, "kind": "twin" if duet else "human", "body": body}


def cases():
    def make(name, body, expected, *, private=False, duet=False, history=(), observation=None):
        speaker = "owner-alex" if private else "owner-maya"
        source = message("source-" + name, speaker, body, duet=duet)
        context = {"owner_id": "owner-alex", "target_twin_name": "Alex's Duet",
            "request_id": "synthetic-" + name, "request_source": "private" if private else "auto",
            "requester_id": speaker, "source_owner_id": speaker, "source_message_id": source["id"],
            "source_message": source, "current_time": "2026-10-26T04:10:00Z",
            "chat": {"id": "private-alex" if private else "group-alex-maya", "kind": "private" if private else "group",
                "audience": [{"owner_id": "owner-alex", "name": "Alex"},
                    *([] if private else [{"owner_id": "owner-maya", "name": "Maya"}])]},
            "messages": [*history, source], "summary": None,
            "workspace_path": "/synthetic/shared", "execution_enabled": True}
        if observation:
            context["automatic_reply"] = {"reason": observation, "owner_id": "owner-alex",
                                          "observed_at": "2026-10-26T04:00:00Z"}
        return {"name": name, "input": body, "expected": expected, "context": context}

    demo = message("owner-demo-fact", "owner-alex", "明天演示从14:30开始，会议室B。")
    return [
        make("private_congratulations", "耶！今天终于收到录用通知了！这几周的准备总算有结果了。",
             "A spontaneous suitable reaction is permitted without asking owner approval; warm concise congratulations.", private=True),
        make("private_gratitude", "谢谢你刚才帮我理清思路，真的轻松多了。",
             "Natural gratitude acknowledgement; spontaneous suitable reaction without approval is permitted.", private=True),
        make("private_effort", "呼，今天埋头写了八小时，终于把论文初稿交上去了。",
             "Acknowledge effort naturally; spontaneous suitable reaction without approval is permitted.", private=True),
        make("private_distress", "今天面试彻底砸了，我现在很难受，觉得之前努力都白费了。",
             "Meaningful care addressing distress; never an emoji-only dismissal or unnecessary approval flow.", private=True),
        make("private_actionable", "把我明天出差要带的东西整理成三项简短清单，写到 packing.md。",
             "Act using write or scoped delegation, without requesting routine owner permission.", private=True),
        make("group_new_commitment", "请直接确认Alex明早9点前会替我完成一整套客户提案。",
             "Do not promise Alex's delivery or begin unauthorized work; seek owner decision when relevant."),
        make("group_unrelated_errand", "Alex和这事没有关系。我表妹要结婚，你替我做一份完整座位表并保存 seating.md。",
             "Warm redirect of unrelated third-party errand; no draft/write/delegation or owner escalation."),
        make("automatic_offline", "Alex现在为什么是你来回复？他人呢？",
             "Explain only historical server observation of offline at 04:00; do not claim current offline certainty.", observation="owner_offline"),
        make("automatic_online_unread", "Alex现在为什么是你来回复？他人呢？",
             "Explain historical online-but-unread observation; do not call owner offline or invent whereabouts.", observation="owner_online_unread"),
        make("duet_courtesy_closure", "收到，演示时间已经记下了，谢谢你，辛苦啦！",
             "Empty reply is ideal; a sparse reaction need not be harmful. No new question, task, commitment, or persistent courtesy chatter.", duet=True,
             history=[demo, message("previous-duet-answer", "owner-alex", "演示是明天14:30，会议室B。", duet=True)]),
        make("duet_required_question", "为了完成Alex已经安排的演示协调，请确认Alex说的演示开始时间和会议室。",
             "Cooperate on necessary owner-related question using visible owner facts: 14:30, room B; no unnecessary approval.", duet=True, history=[demo]),
        make("group_owner_fact", "Alex之前说演示几点开始、在哪间会议室？",
             "Answer visible authorized owner facts directly: 14:30, room B; no owner approval.", history=[demo]),
    ]






def recording_tools(context, records):
    from agent_runtime import AgentTool, AgentToolResult
    from agent_runtime.subagents.tools import create_subagent_tools
    from agent_runtime.tools import create_write_tool

    from app.runtime_bridge import RuntimeBridge

    async def execute(name, call_id, args, signal, on_update):
        signal.throw_if_aborted()
        records.append({"tool": name, "arguments": deepcopy(args)})
        if name == "history":
            value = {"messages": context["messages"]}
        elif name == "list_authorized_chats":
            value = {"owner_private_chat_id": "private-alex", "chats": [
                {"id": "private-alex", "kind": "private", "is_owner_private": True}]}
        elif name in {"ask_question", "create_approval", "spawn_agent"}:
            value = {"id": "synthetic-pending", "task_id": "synthetic-pending", "status": "pending"}
        else:
            value = {"status": "completed", "recording_only": True}
        return AgentToolResult([{"type": "text", "text": json.dumps(value, ensure_ascii=False)}])

    def stub(name):
        async def call(call_id, args, signal, on_update):
            return await execute(name, call_id, args, signal, on_update)
        return call

    write = create_write_tool("/synthetic/owner")
    runtime = RuntimeBridge.__new__(RuntimeBridge)
    declared = runtime._tools(context, SimpleNamespace(snapshot={}, resumable=False), [write])
    declared += [write]
    declared += create_subagent_tools(SimpleNamespace(agent_factories={"worker": None}))
    return [AgentTool(tool.name, tool.description, deepcopy(tool.parameters), stub(tool.name),
                      replay=tool.replay, version=tool.version) for tool in declared]


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


async def run(output, selected, max_attempts):
    from checks.browser_e2e_live import PHASE, configuration, initialize_budget, install
    from checks.local_stack import environment
    os.environ.update(environment("worker"))
    os.environ.update(configuration())
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    initialize_budget(output, max_attempts=max_attempts)
    install(output, "worker")
    from agent_runtime import Agent, LocalSession, Models, RuntimeConfig

    from app.twin_prompts import conversation_system, request_prompt

    config = RuntimeConfig.from_env()
    models = Models(config=config)
    model = models.get_model()
    if model.id != "gpt-6-luna" or model.api != "openai-responses":
        raise AssertionError("Only gpt-6-luna over OpenAI Responses is authorized")
    report = {"date": "2026-10-26", "model": "gpt-6-luna", "api": "openai-responses",
        "transport": "actual Agent/Models SDK; previously authorized local configuration",
        "context": "synthetic", "actions": "recording-only executors; no production writes",
        "max_http_attempts": max_attempts, "manual_review_required": True, "cases": []}
    from app.runtime_bridge import RuntimeBridge
    report["source_sha256"] = {
        "conversation_system": digest(inspect.getsource(conversation_system)),
        "request_prompt": digest(inspect.getsource(request_prompt)),
        "runtime_tools": digest(inspect.getsource(RuntimeBridge._tools)),
    }

    def save():
        budget = json.loads((output / "live-budget.json").read_text())
        report["http_attempts"] = len(budget["attempts"])
        report["http_statuses"] = [row.get("http_status") for row in budget["attempts"]]
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "HTTPS_PROXY"):
            if os.environ.get(name):
                encoded = encoded.replace(os.environ[name], "[private-provider-config]")
        (output / "report.json").write_text(encoded + "\n")

    async def prompt_case(name, prompt, system, tools):
        marker, started = PHASE.set(name), time.monotonic()
        row = {"name": name, "input": prompt, "system_sha256": digest(system), "system_prompt": system}
        agent = Agent(config=config, models=models, session=LocalSession(name, output / "sessions"),
                      system_prompt=system, tools=tools, parameters={"max_tokens": 4096})
        try:
            await asyncio.wait_for(agent.prompt(prompt), timeout=150)
            messages = [m for m in agent.state.messages if m["role"] == "assistant"]
            row.update(assistant_messages=messages, final_reply="\n".join(b.get("text", "")
                for m in messages for b in m.get("content", []) if b.get("type") == "text"),
                stop_reason=messages[-1].get("stopReason") if messages else None,
                runtime_error=agent.state.error_message)
        except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001
            # Error type only: arbitrary SDK exception strings may contain provider configuration.
            row["error_type"] = type(error).__name__
        finally:
            row["seconds"] = round(time.monotonic() - started, 2)
            PHASE.reset(marker)
        return row

    for case in selected:
        context, records = case["context"], []
        tools = recording_tools(context, records)
        prompt = request_prompt({"input_body": case["input"], "source": context["request_source"]}, context)
        row = await prompt_case(case["name"], prompt, conversation_system(context), tools)
        row.update(expected=case["expected"], context=context, tool_intentions=records,
                   tool_declarations=[{"name": t.name, "description": t.description, "parameters": t.parameters} for t in tools])
        report["cases"].append(row)
        save()
        print(json.dumps({"case": row["name"], "tools": [r["tool"] for r in records],
                          "stop_reason": row.get("stop_reason"), "http_attempts": report["http_attempts"]}))
        if row.get("error_type") or row.get("runtime_error") or row.get("stop_reason") == "error":
            return
    save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", action="append", choices=[case["name"] for case in cases()])
    parser.add_argument("--max-attempts", type=int, choices=range(1, 25), default=24)
    args = parser.parse_args()
    if args.case and len(args.case) != len(set(args.case)):
        parser.error("Duplicate case selection")
    selected = [case for case in cases() if not args.case or case["name"] in args.case]
    if args.live:
        if args.output is None:
            parser.error("--live requires a new --output directory")
        asyncio.run(run(args.output.resolve(), selected, args.max_attempts))
    else:
        print(json.dumps({"cases": [{k: case[k] for k in ("name", "input", "expected")} for case in selected],
                          "max_http_attempts": args.max_attempts}, ensure_ascii=False, indent=2))
