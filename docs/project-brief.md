# twinteams project brief

## Product direction

Build a React frontend and a FastAPI backend running strictly on Python 3.13.x.
The intended interface reproduces the approved Microsoft Teams chat reference
1:1. The self-chat entry will become a conversation with the user's own digital
twin. Chat behavior, persistence, authentication, and deployment are not yet
specified.

## Staged workflow and approval gates

1. **Workspace foundation:** Create an isolated project scaffold,
   tooling, health endpoint, tests, and documentation only.
2. **Reference approval:** Obtain and confirm the user's screenshot before any
   UI design or implementation. Do not infer layouts, assets, or visual details.
3. **UI implementation (current stage):** Build the approved chat interface. Complete UI work
   before proceeding to the runtime stage.
4. **Runtime integration:** Wait for the user's explicit direction after the UI
   is finished before integrating agent-runtime or implementing the digital twin.

## Current boundaries

- The approved Teams-inspired UI now includes conversation and reminder views.
- The rail contains only 提醒 and 对话. Data and interactions are local mock state.
- People search is UI and typed contract only; no implementation or backend route.
- The backend implements only `GET /health` returning `{"status":"ok"}`.
- No runtime imports, credentials, chat APIs, or external service integration.
- `/workspace/shared/agent-runtime` is a separate existing project and must remain
  unchanged by this scaffold.
- No remote repository, push, deployment, or public preview is part of this stage.
