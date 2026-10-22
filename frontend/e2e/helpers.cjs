const assert = require("node:assert/strict");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function poll(
  fn,
  label = "condition",
  { timeout = 20000, interval = 100 } = {},
) {
  const end = Date.now() + timeout;
  do {
    const value = await fn();
    if (value) return value;
    await sleep(interval);
  } while (Date.now() < end);
  throw new Error(`Timed out waiting for ${label}`);
}
async function api(page, pathname, { status = 200 } = {}) {
  const response = await page.request.get(
    new URL(
      pathname.startsWith("/api/") ? pathname : `/api${pathname}`,
      page.url(),
    ).href,
  );
  assert.equal(response.status(), status, `GET ${pathname} status`);
  if (status === 204) return null;
  return response.json();
}
async function login(page, code = "DEV-AAA") {
  await page.getByPlaceholder("DEV-AAA").fill(code);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("button", { name: "Account", exact: true }).waitFor();
  return api(page, "/auth/me");
}
async function logout(page) {
  await page.getByRole("button", { name: "Account", exact: true }).click();
  await page
    .getByRole("button", { name: "Sign out / switch account", exact: true })
    .click();
  await page.getByPlaceholder("DEV-AAA").waitFor();
}
async function openTwinTab(page, tab = "Profile") {
  await page.getByRole("button", { name: "Account", exact: true }).click();
  await page.getByRole("menuitem", { name: "My Twin", exact: true }).click();
  await page.getByRole("tab", { name: tab, exact: true }).click();
}
async function sendMessage(
  page,
  text,
  {
    key = "Enter",
    waitCanonical = true,
    needle = text.split("\n").find((line) => line.trim()) || text,
  } = {},
) {
  const input = page.locator(".composer textarea");
  await input.fill(text);
  await input.press(key);
  const rows = page
    .locator(
      waitCanonical
        ? "article.own[data-message-id]"
        : "article.own[data-message-id], article.own[data-local-send]",
    )
    .filter({ has: page.locator(".message-text").filter({ hasText: needle }) });
  await rows.last().waitFor();
  return rows.last();
}
async function privateChat(page) {
  const bootstrap = await api(page, "/bootstrap");
  const chat = bootstrap.chats.find((item) => item.kind === "private");
  assert.ok(chat, "owner has a private chat");
  const row = page
    .locator(".conversation-list .chat-item")
    .filter({ hasText: chat.title })
    .first();
  await row.waitFor({ state: "attached" });
  if (!(await row.isVisible()))
    await page
      .getByRole("button", { name: "Show or hide chat list", exact: true })
      .click();
  await row.click();
  await page.locator(".composer textarea").waitFor();
  return chat;
}
async function chooseChat(page, title) {
  const row = page
    .locator(".conversation-list .chat-item")
    .filter({ hasText: title })
    .first();
  await row.waitFor({ state: "attached" });
  if (!(await row.isVisible()))
    await page
      .getByRole("button", { name: "Show or hide chat list", exact: true })
      .click();
  await row.click();
  await page.locator(".composer textarea").waitFor();
}
module.exports = {
  assert,
  sleep,
  poll,
  api,
  login,
  logout,
  openTwinTab,
  sendMessage,
  privateChat,
  chooseChat,
};
