# Duet social acceptance — 2026-10-26

Accepted after manual review of visible replies, tool intentions and router decisions. The bounded campaign reserved **22 of 24 permitted HTTP attempts**, using only **gpt-6-luna / OpenAI Responses** through the previously authorized local worker configuration. All 22 requests received HTTP 200 headers; one initial model stream was interrupted before an accepted visible result. No more provider calls were made.

The [structured transcript](duet-social-acceptance-2026-10-26.json) preserves all 14 completed main observations, three router observations, inputs, visible text, tool arguments, numeric usage and source/prompt digests. Hidden thinking, signatures, encrypted provider content and raw provider identifiers are omitted. Private raw transcripts remain under `/tmp`.

## Initial evidence and changed prompt

The first congratulations, gratitude and effort cases returned warm text but **zero reaction tool calls**. This was an evidence gap for spontaneous reaction use, rather than a claim that every warm text response requires an emoji. The initial replies are retained. The campaign stopped while the following group-commitment request was in flight; its seventh reserved attempt has no accepted visible result.

The revised main prompt explicitly names `set_message_reaction` for selective brief social acknowledgement. Two natural positive inputs were rerun after that change; neither asks for a reaction. There were no repeated unchanged runs.

## Reviewed behavior

| Case | Observed result | Assessment |
| --- | --- | --- |
| Private congratulations | `set_message_reaction` with 🎉, then warm congratulations | Voluntary reaction; no permission question or action announcement |
| Private gratitude | `set_message_reaction` with ❤️, then a brief acknowledgement | Voluntary reaction; no permission question or action announcement |
| Private effort, initial prompt | Warm recognition of eight hours of work; no reaction | Optional reaction not demonstrated in this case; retained |
| Owner distress, initial prompt | Substantive care addressing the failed interview and effort | Meaningful response, not emoji-only |
| Owner file task, initial prompt | Recorded `write` with a three-item packing list, then completion text | Routine authorized action taken without approval; actual file write is stubbed |
| New group commitment | No promise, execution or delegation; asks about relevance and leaves the decision to Alex | Scope preserved; phrasing is fairly formal |
| Unrelated third-party errand | Declines wedding seating work; no write, delegation or owner escalation | Scope preserved; polite but fairly formal |
| Automatic offline | Trigger prompt contains historical offline observation and timestamp; reply describes past observation and current uncertainty | Truthful historical context |
| Automatic online-unread | Trigger prompt contains historical online-but-unread observation and timestamp; reply offers coverage without claiming offline status or whereabouts | Prompt inclusion checked; exact cause was not verbalized |
| Duet courtesy closure | One 🙏 reaction, **empty visible text**, no new question/task/commitment | Sparse reaction accepted; no conversational continuation |
| Necessary Duet coordination question | Answers visible owner facts: tomorrow 14:30, room B | Useful cooperation without approval or new commitment |
| Ordinary group owner fact | Answers tomorrow 14:30, room B directly | No unnecessary approval |

All three selected reactions (🎉, ❤️, 🙏) are present in the actual pinned backend reaction registry, target the correct visible source message, use `active=true`, and use the current chat by default. This validates the selected Unicode and intent; live server reaction execution is not claimed.

Both automatic contexts rendered the correct observed reason and `2026-10-26T04:00:00Z` in the real system prompt, with current server time ten minutes later. The online-unread reply did not disclose the presence detail; it did not invent current presence or whereabouts.

## Router and regressions

The model received the actual production `route_targets` system assignment evaluated from its AST, without a separately copied classifier prompt. The courtesy inputs include explicit Duet addressing.

| Duet-authored input | Real model decision |
| --- | --- |
| Thanks | `needs_response=false`, no targets |
| Completed-work closure | `needs_response=false`, no targets |
| “Alex明天14:30方便参加项目同步吗？需要他确认一下。” | `needs_response=true`, target `owner-alex` |

The availability question contains no prior authorization claim. The router admits the conversational need; the main Duet still checks actual action authority.

Independent regression command:

```text
pytest -q tests/test_reactions.py::test_desired_state_actor_multiemoji_revision_and_no_model_triggers tests/test_group_trigger_matrix.py::test_published_duets_branch_to_both_other_owners_share_six_and_continue
```

**4 passed in 8.07s**, using real disposable PostgreSQL schemas and an offline model. Reaction changes create no new `ReplyRequest` or `RoutingDecision`; three-owner Duet branches share six automatic acceptances and pause the seventh until explicit continuation.

The coordinating agent separately reported 48 trigger/group-matrix checks, 40 final prompt/SDK-factory checks and 12 earlier follow-up/nested checks passing. The four independently repeated checks overlap that coverage; these counts are not summed as unique tests.

## Evidence boundaries

The main cases use real `conversation_system` / `request_prompt`, production `RuntimeBridge._tools` declarations and SDK write/subagent declarations, with recording-only executors. Model choice, tool arguments and post-tool text are real; production action execution is not. Router model decisions are real; its storage/admission path is covered by separate regressions.

This is a small manually reviewed acceptance sample, not a statistical or deterministic guarantee. Distress and the ordinary file task were observed before the final reaction wording revision; their relevant care/action rules remained unchanged. There were no production messages, files, delegation, approvals, service restarts or database writes in this acceptance work.

The coordinating task subsequently verified rollout: worker and both APIs are alive; local ports 8000/8001 and public health returned HTTP 200; FRPC remained unchanged. Full message/chat-sequence hashes were unchanged across 214 messages (94 human) and seven chats, with zero active queues/leases. Rollout evidence is `/tmp/duet-trigger-rollout-20261026.json`; rollout was outside this acceptance harness.
