/** Small real-app presentation check. No message sends or provider calls. */
const {
  assert,
  api,
  poll,
  privateChat,
  openTwinTab,
  chooseChat,
} = require("./helpers.cjs");
const { startChat, workspace } = require("./conversations.cjs");
const path = require("node:path");

async function appearance(avatar) {
  return avatar.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      background: style.backgroundColor,
      foreground: style.color,
      text: element.textContent.trim(),
      image: !!element.querySelector("img"),
      brand: !!element.querySelector(".brand-mark"),
      badge: !!element.querySelector(".twin-badge"),
    };
  });
}
function contrast(background, foreground) {
  const luminance = (value) => {
    const channels = value
      .match(/[\d.]+/g)
      .slice(0, 3)
      .map(Number)
      .map((channel) => {
        const s = channel / 255;
        return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      });
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  };
  const a = luminance(background),
    b = luminance(foreground);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}
function readable(style) {
  const ratio = contrast(style.background, style.foreground);
  assert.ok(
    ratio >= 4.5,
    `avatar text contrast ${ratio.toFixed(2)} must be readable`,
  );
  return Number(ratio.toFixed(3));
}
const profileAvatar = (page) => page.locator(".twin-profile-avatar > .avatar");
const sameColors = (actual, expected) => {
  assert.equal(actual.background, expected.background);
  assert.equal(actual.foreground, expected.foreground);
};
const cases = [
  {
    name: "avatars / Fluent name colors stay readable and consistent across contacts, profiles and reload; explicit images and presets win",
    run: async (t) => {
      const a = await t.session();
      const bootstrap = await api(a.page, "/bootstrap");
      const humans = ["AAA", "BBB", "CCC"].map((name) => {
        const actor = bootstrap.actors.find(
          (row) => row.kind === "human" && row.name === name,
        );
        assert.ok(actor, `isolated ${name} actor exists`);
        return actor;
      });
      const title = t.unique("avatar-members");
      await startChat(a.page, ["BBB", "CCC"], title);
      await workspace(a.page);
      await poll(
        async () =>
          (await a.page.locator(".members-section .member-row").count()) === 3,
        "three real group members",
      );
      const groupAvatar = a.page.locator(".chat-header > .avatar.group-avatar");
      const groupBefore = await appearance(groupAvatar);
      assert.equal(groupBefore.brand, false);
      assert.equal(await groupAvatar.locator("svg").count(), 1);
      const humanStyles = {};
      for (const actor of humans) {
        const avatar = a.page
          .locator(".members-section .member-row")
          .filter({ hasText: actor.name })
          .locator(":scope > .avatar");
        const style = await appearance(avatar);
        assert.equal(style.text, actor.initials);
        assert.equal(style.image, false);
        humanStyles[actor.id] = {
          ...style,
          name: actor.name,
          contrast: readable(style),
        };
      }
      assert.equal(
        new Set(Object.values(humanStyles).map((row) => row.background)).size,
        3,
        "these three named fixture users have distinct default backgrounds",
      );
      sameColors(
        await appearance(a.page.locator(".account-button > .avatar")),
        humanStyles[bootstrap.current_owner_id],
      );
      await a.page.reload();
      await chooseChat(a.page, title);
      await workspace(a.page);
      await a.page.locator(".members-section .member-row").first().waitFor();
      for (const actor of humans) {
        sameColors(
          await appearance(
            a.page
              .locator(".members-section .member-row")
              .filter({ hasText: actor.name })
              .locator(":scope > .avatar"),
          ),
          humanStyles[actor.id],
        );
      }
      sameColors(await appearance(groupAvatar), groupBefore);
      await a.page.screenshot({
        path: path.join(t.artifactDir, "avatar-group-members.png"),
        fullPage: true,
      });
      t.record("group_members_screenshot", "avatar-group-members.png");

      const twinStyles = {};
      for (const [index, human] of humans.entries()) {
        const session = index === 0 ? a : await t.session(`DEV-${human.name}`);
        const { page } = session;
        await privateChat(page);
        const twin = bootstrap.actors.find(
          (row) => row.kind === "twin" && row.owner_id === human.id,
        );
        const header = await appearance(page.locator(".chat-header > .avatar"));
        assert.ok(
          header.brand && header.badge && !header.image,
          "default Twin keeps its brand and badge",
        );
        sameColors(
          await appearance(page.locator(".account-button > .avatar")),
          humanStyles[human.id],
        );
        await openTwinTab(page, "Profile");
        sameColors(await appearance(profileAvatar(page)), header);
        sameColors(
          await appearance(page.locator(".twin-account-reference > .avatar")),
          humanStyles[human.id],
        );
        await page.reload();
        await profileAvatar(page).waitFor();
        const after = await appearance(profileAvatar(page));
        sameColors(after, header);
        assert.ok(after.brand && after.badge);
        twinStyles[twin.id] = {
          ...after,
          name: twin.name,
          contrast: readable(after),
        };
        const screenshot = `avatar-${human.name.toLowerCase()}-default-twin.png`;
        await page.screenshot({
          path: path.join(t.artifactDir, screenshot),
          fullPage: true,
        });
        t.record(`${human.name}_default_screenshot`, screenshot);
      }
      assert.equal(
        new Set(Object.values(twinStyles).map((row) => row.background)).size,
        3,
        "these three default Twin names also have distinct backgrounds",
      );
      t.record("default_actor_colors", {
        humans: humanStyles,
        twins: twinStyles,
        stable_seed: "actor name, fallback id",
        global_collision_freedom_claimed: false,
      });

      await a.page
        .getByRole("button", { name: "Choose avatar", exact: true })
        .click();
      await a.page
        .getByRole("button", { name: "Default avatar 3", exact: true })
        .click();
      let modal = a.page.locator(".modal[role=dialog]");
      await modal.getByRole("button", { name: "Save", exact: true }).click();
      await modal.waitFor({ state: "hidden" });
      await poll(
        async () =>
          (await api(a.page, "/me/twin")).actor.avatar_preset === "wave",
        "real saved preset",
      );
      await a.page.reload();
      await profileAvatar(a.page).locator(".preset-avatar").waitFor();
      assert.equal(
        await profileAvatar(a.page).locator(".brand-mark").count(),
        0,
      );
      await a.page
        .getByRole("button", { name: "Remove avatar", exact: true })
        .click();
      await profileAvatar(a.page).locator(".brand-mark").waitFor();
      sameColors(
        await appearance(profileAvatar(a.page)),
        twinStyles["twin-alex"],
      );

      const image = await a.page.evaluate(() => {
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = 32;
        const context = canvas.getContext("2d");
        context.fillStyle = "#1674bb";
        context.fillRect(0, 0, 32, 32);
        context.fillStyle = "#fff";
        context.fillRect(8, 8, 16, 16);
        return canvas.toDataURL("image/png").split(",")[1];
      });
      await a.page
        .getByRole("button", { name: "Choose avatar", exact: true })
        .click();
      modal = a.page.locator(".modal[role=dialog]");
      await modal.locator("input[type=file]").setInputFiles({
        name: "avatar-check.png",
        mimeType: "image/png",
        buffer: Buffer.from(image, "base64"),
      });
      await modal.locator(".twin-crop canvas").waitFor();
      await modal.getByRole("button", { name: "Save", exact: true }).click();
      await modal.waitFor({ state: "hidden" });
      const saved = await poll(async () => {
        const actor = (await api(a.page, "/me/twin")).actor;
        return actor.avatar_url && !actor.avatar_preset && actor;
      }, "real uploaded avatar");
      await poll(
        async () =>
          profileAvatar(a.page)
            .locator("img")
            .evaluate((img) => img.complete && img.naturalWidth > 0),
        "actual uploaded avatar bytes loaded",
      );
      assert.equal(
        await profileAvatar(a.page).locator(".brand-mark").count(),
        0,
      );

      const resource = new URL(saved.avatar_url, a.page.url()).href;
      let injected = 0;
      await a.page.route(
        resource,
        async (route) => {
          assert.equal(route.request().resourceType(), "image");
          injected++;
          await route.fulfill({
            status: 404,
            contentType: "image/png",
            body: "",
          });
        },
        { times: 1 },
      );
      await a.page.reload();
      await profileAvatar(a.page).locator(".brand-mark").waitFor();
      sameColors(
        await appearance(profileAvatar(a.page)),
        twinStyles["twin-alex"],
      );
      assert.equal(injected, 1);
      t.record("image_resource_fault", {
        exact_avatar_image_only: true,
        responses_injected: injected,
        status: 404,
        business_JSON_intercepted: false,
        websocket_intercepted: false,
      });
      await a.page
        .getByRole("button", { name: "Remove avatar", exact: true })
        .click();
      await poll(
        async () => !(await api(a.page, "/me/twin")).actor.avatar_url,
        "uploaded test avatar removed through UI",
      );
      t.record("provider_calls_required", 0);
    },
  },
];
module.exports = { cases };
