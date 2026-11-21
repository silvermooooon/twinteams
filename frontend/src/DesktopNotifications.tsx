import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
import type { Actor, Chat } from "./api/client";
import { LiquiGlass } from "@liqui-design/glass";
import { Switch } from "@/components/ui/switch";
import { LIQUI_BAR_GLASS } from "@/lib/liquiPresets";
import { t } from "./i18n";
import { chatTitle, Icon, IconButton, Modal } from "./ui";
import "./DesktopNotifications.css";

type Preferences = { enabled: boolean; dismissed: boolean };
type Latest = { id: string | null; revision?: number; unread: boolean };
const BRAND_ICON = "/assets/duet-symbol.svg";
const MAX_CLAIMS = 100;
const MAX_READ_IDS = 1000;
function readPreferences(key: string): Preferences {
  try {
    const saved = JSON.parse(localStorage.getItem(key) || "{}");
    return {
      enabled: saved.enabled !== false,
      dismissed: saved.dismissed === true,
    };
  } catch {
    return { enabled: true, dismissed: false };
  }
}
function browserPermission(): NotificationPermission {
  return typeof Notification === "undefined"
    ? "default"
    : Notification.permission;
}
function background() {
  return document.visibilityState === "hidden" || !document.hasFocus();
}

/** Only committed, already-authorized chat snapshots enter this hook. No notification fetches. */
export function useDesktopNotifications(
  ownerId: string,
  onNavigate: (chatId: string) => void,
) {
  const navigate = useRef(onNavigate);
  navigate.current = onNavigate;
  const currentOwner = useRef(ownerId);
  currentOwner.current = ownerId;
  const [, update] = useReducer((value) => value + 1, 0);
  const [permission, setPermission] =
    useState<NotificationPermission>(browserPermission);
  const [requesting, setRequesting] = useState(false);
  const [runtimeError, setRuntimeError] = useState(false);
  const supported = typeof Notification !== "undefined";
  const secure = window.isSecureContext;
  const scope = useMemo(() => {
    const preferenceKey = `twinteams.notifications.preferences:${encodeURIComponent(ownerId)}`;
    return {
      ownerId,
      preferenceKey,
      claimKey: `twinteams.notifications.claims:${encodeURIComponent(ownerId)}`,
      readKey: `twinteams.notifications.read:${encodeURIComponent(ownerId)}`,
      preferences: readPreferences(preferenceKey),
      active: true,
      initializedAt: 0,
      seen: new Map<string, number>(),
      read: new Set<string>(),
      latest: new Map<string, Latest>(),
      notifications: new Map<string, { notification: Notification; messageId: string }>(),
    };
  }, [ownerId]);
  const close = useCallback(
    (chatId?: string, messageIds?: string[]) => {
      for (const [id, { notification, messageId }] of scope.notifications) {
        if (chatId !== undefined && id !== chatId) continue;
        if (messageIds && !messageIds.includes(messageId)) continue;
        notification.close();
        scope.notifications.delete(id);
      }
    },
    [scope],
  );
  const revoke = useCallback(
    (chatId?: string) => {
      if (chatId === undefined) scope.latest.clear();
      else scope.latest.delete(chatId);
      close(chatId);
    },
    [scope, close],
  );
  const applyRead = useCallback((chatId: string, messageIds: string[]) => {
    for (const id of messageIds) scope.read.add(id);
    while (scope.read.size > MAX_READ_IDS) scope.read.delete(scope.read.values().next().value!);
    close(chatId, messageIds);
    const latest = scope.latest.get(chatId);
    if (latest?.id && messageIds.includes(latest.id)) latest.unread = false;
  }, [scope, close]);
  const acknowledgeRead = useCallback((chatId: string, messageIds: string[]) => {
    applyRead(chatId, messageIds);
    try {
      localStorage.setItem(scope.readKey, JSON.stringify({ chat_id: chatId, message_ids: messageIds }));
    } catch {
      /* Exact dismissal still works in this tab when browser storage is unavailable. */
    }
  }, [scope, applyRead]);
  const stop = useCallback(() => {
    scope.active = false;
    close();
  }, [scope, close]);
  const savePreferences = useCallback(
    (patch: Partial<Preferences>) => {
      scope.preferences = { ...scope.preferences, ...patch };
      try {
        localStorage.setItem(
          scope.preferenceKey,
          JSON.stringify(scope.preferences),
        );
      } catch {
        /* This tab's controls still work without browser storage. */
      }
      if (!scope.preferences.enabled) close();
      update();
    },
    [scope, close],
  );

  useEffect(() => {
    scope.active = true;
    setPermission(browserPermission());
    setRequesting(false);
    setRuntimeError(false);
    const focus = () => {
      setPermission(browserPermission());
    };
    const storage = (event: StorageEvent) => {
      if (event.key === scope.readKey && event.newValue && scope.active && currentOwner.current === ownerId) {
        try {
          const read = JSON.parse(event.newValue);
          if (read && typeof read.chat_id === "string" && Array.isArray(read.message_ids) &&
              read.message_ids.length <= 1000 && read.message_ids.every((id: unknown) => typeof id === "string"))
            applyRead(read.chat_id, read.message_ids);
        } catch {
          /* Ignore malformed cross-tab metadata; it cannot grant read access. */
        }
      }
      if (event.key === scope.preferenceKey) {
        scope.preferences = readPreferences(scope.preferenceKey);
        if (!scope.preferences.enabled) close();
        update();
      }
    };
    const pagehide = () => close();
    window.addEventListener("focus", focus);
    document.addEventListener("visibilitychange", focus);
    window.addEventListener("storage", storage);
    window.addEventListener("pagehide", pagehide);
    return () => {
      scope.active = false;
      close();
      window.removeEventListener("focus", focus);
      document.removeEventListener("visibilitychange", focus);
      window.removeEventListener("storage", storage);
      window.removeEventListener("pagehide", pagehide);
    };
  }, [scope, ownerId, close, applyRead]);

  const notify = useCallback(
    (chat: Chat, actors: Actor[]) => {
      const meta = chat.last_message_meta;
      if (
        !meta ||
        !meta.unread ||
        scope.read.has(meta.id) ||
        !ownerId ||
        !scope.active ||
        currentOwner.current !== ownerId ||
        !scope.preferences.enabled ||
        !supported ||
        !secure ||
        browserPermission() !== "granted" ||
        !background()
      )
        return;
      const author = actors.find((actor) => actor.id === meta.author_id);
      if (
        !author ||
        meta.kind === "system" ||
        (meta.kind === "human" && meta.author_id === ownerId) ||
        (meta.kind === "reply_for_me" && author.owner_id === ownerId)
      )
        return;
      const eligible = () =>
        scope.active &&
        currentOwner.current === ownerId &&
        scope.preferences.enabled &&
        browserPermission() === "granted" &&
        background() &&
        scope.latest.get(chat.id)?.id === meta.id &&
        scope.latest.get(chat.id)?.unread === true &&
        !scope.read.has(meta.id);
      const claimAndShow = () => {
        if (!eligible()) return;
        // Web Locks serializes this short, synchronous claim. Only bounded metadata is stored.
        let claims: [string, number, string][] = [];
        try {
          const saved: unknown = JSON.parse(
            localStorage.getItem(scope.claimKey) || "[]",
          );
          if (Array.isArray(saved))
            claims = saved
              .filter(
                (item): item is [string, number, string] =>
                  Array.isArray(item) &&
                  item.length === 3 &&
                  typeof item[0] === "string" &&
                  Number.isFinite(item[1]) &&
                  typeof item[2] === "string",
              )
              .slice(-MAX_CLAIMS);
        } catch {
          /* Best-effort per-tab delivery when storage is unavailable. */
        }
        if (claims.some(([id, seq]) => id === chat.id && seq >= meta.seq))
          return;
        const next: [string, number, string][] = [
          ...claims.filter(([id]) => id !== chat.id),
          [chat.id, meta.seq, meta.id],
        ];
        const title =
          chat.kind === "group" && chat.member_owner_ids.length > 2
            ? `${author.name} · ${chatTitle(chat, actors, ownerId)}`
            : author.name;
        try {
          const notification = new Notification(title, {
            body:
              chat.last_message?.replace(/\s+/g, " ").trim().slice(0, 180) ||
              t("notifications.attachment"),
            icon: BRAND_ICON,
            tag: `twinteams:${ownerId}:${chat.id}`,
          });
          close(chat.id);
          scope.notifications.set(chat.id, { notification, messageId: meta.id });
          try {
            localStorage.setItem(
              scope.claimKey,
              JSON.stringify(next.slice(-MAX_CLAIMS)),
            );
          } catch {
            /* Never persist message text or require storage to deliver. */
          }
          notification.onclick = (event) => {
            event.preventDefault();
            notification.close();
            if (scope.notifications.get(chat.id)?.notification === notification)
              scope.notifications.delete(chat.id);
            if (
              scope.active &&
              currentOwner.current === ownerId &&
              scope.latest.has(chat.id)
            ) {
              window.focus();
              navigate.current(chat.id);
            }
          };
          notification.onclose = () => {
            if (scope.notifications.get(chat.id)?.notification === notification)
              scope.notifications.delete(chat.id);
          };
        } catch {
          if (scope.active && currentOwner.current === ownerId)
            setRuntimeError(true);
        }
      };
      if (navigator.locks)
        void navigator.locks
          .request(`twinteams:notifications:${ownerId}`, () => {
            claimAndShow();
          })
          .catch(() => {
            if (scope.active && currentOwner.current === ownerId)
              setRuntimeError(true);
          });
      else claimAndShow();
    },
    [scope, ownerId, close, supported, secure],
  );

  const observe = useCallback(
    (chats: Chat[], actors: Actor[], full = false, serverTime?: string) => {
      if (!ownerId || !scope.active || currentOwner.current !== ownerId) return;
      const first = full && scope.initializedAt === 0;
      if (full) {
        const allowed = new Set(chats.map((chat) => chat.id));
        for (const id of scope.latest.keys())
          if (!allowed.has(id)) {
            scope.latest.delete(id);
            close(id);
          }
      }
      for (const chat of chats) {
        if (!full && !scope.latest.has(chat.id)) continue;
        const accepted = scope.latest.get(chat.id);
        // A late summary must not suppress a newer notification waiting for its lock.
        // Revision, rather than seq, still permits a new withdrawal to lower last seq.
        if (
          accepted?.revision !== undefined &&
          (chat.revision === undefined || chat.revision < accepted.revision)
        )
          continue;
        const meta = chat.last_message_meta;
        const previous = scope.seen.get(chat.id);
        scope.latest.set(chat.id, {
          id: meta?.id || null,
          revision: chat.revision,
          unread: meta?.unread === true && !scope.read.has(meta.id),
        });
        if (!meta) {
          close(chat.id);
          continue;
        }
        if (
          scope.notifications.has(chat.id) &&
          (chat.unread_count === 0 || meta.seq < (previous || 0) ||
            scope.notifications.get(chat.id)?.messageId === meta.id && !meta.unread)
        )
          close(chat.id);
        // Never lower a high-water mark after withdrawal, stale responses, or history changes.
        scope.seen.set(chat.id, Math.max(previous || 0, meta.seq));
        if (
          !first &&
          scope.initializedAt &&
          meta.seq > (previous || 0) &&
          Date.parse(meta.created_at) > scope.initializedAt
        )
          notify(chat, actors);
      }
      // Initial synchronization is conservative: messages already present here are history.
      if (first) {
        const serverTimestamp = Date.parse(serverTime || "");
        scope.initializedAt = Number.isFinite(serverTimestamp)
          ? serverTimestamp
          : Date.now();
      }
      while (scope.seen.size > 500)
        scope.seen.delete(scope.seen.keys().next().value!);
    },
    [scope, ownerId, notify, close],
  );
  const observeBootstrap = useCallback(
    (chats: Chat[], actors: Actor[], serverTime?: string) =>
      observe(chats, actors, true, serverTime),
    [observe],
  );
  const observeChat = useCallback(
    (chat: Chat, actors: Actor[]) => observe([chat], actors),
    [observe],
  );
  const enable = useCallback(async () => {
    if (!supported || !secure || requesting || browserPermission() === "denied")
      return;
    setRequesting(true);
    setRuntimeError(false);
    try {
      const result =
        browserPermission() === "granted"
          ? "granted"
          : await Notification.requestPermission();
      if (!scope.active || currentOwner.current !== ownerId) return;
      setPermission(result);
      if (result === "granted")
        savePreferences({ enabled: true, dismissed: true });
    } catch {
      if (scope.active && currentOwner.current === ownerId)
        setRuntimeError(true);
    } finally {
      if (scope.active && currentOwner.current === ownerId)
        setRequesting(false);
    }
  }, [scope, ownerId, supported, secure, requesting, savePreferences]);
  return {
    supported,
    secure,
    permission,
    requesting,
    runtimeError,
    enabled: scope.preferences.enabled && permission === "granted",
    showPrompt:
      !!ownerId &&
      supported &&
      secure &&
      permission === "default" &&
      !scope.preferences.dismissed,
    enable,
    disable: () => savePreferences({ enabled: false }),
    dismiss: () => savePreferences({ dismissed: true }),
    close,
    acknowledgeRead,
    revoke,
    stop,
    observeBootstrap,
    observeChat,
  };
}

type Controller = ReturnType<typeof useDesktopNotifications>;
export function DesktopNotificationPrompt({
  controller,
}: {
  controller: Controller;
}) {
  if (!controller.showPrompt) return null;
  return (
    <LiquiGlass
      {...LIQUI_BAR_GLASS}
      radius={18}
      className="desktop-notification-prompt lq-chrome-bar"
      contentClassName="desktop-notification-prompt-inner"
      role="complementary"
      aria-label={t("notifications.prompt")}
    >
      <Icon name="bell" size={18} />
      <div>
        <strong>{t("notifications.prompt")}</strong>
        <p>{t("notifications.promptDetail")}</p>
      </div>
      <button
        className="secondary-button lg-interactive"
        type="button"
        disabled={controller.requesting}
        onClick={() => {
          void controller.enable();
        }}
      >
        {t("notifications.turnOn")}
      </button>
      <IconButton
        icon="close"
        label={t("common.dismiss")}
        onClick={controller.dismiss}
      />
    </LiquiGlass>
  );
}
export function DesktopNotificationSettings({
  controller,
  onClose,
}: {
  controller: Controller;
  onClose: () => void;
}) {
  const unavailable =
    !controller.supported ||
    !controller.secure ||
    controller.permission === "denied";
  return (
    <Modal title={t("notifications.title")} onClose={onClose}>
      <div className="desktop-notification-setting">
        <div>
          <h3>{t("notifications.desktop")}</h3>
          <p>{t("notifications.description")}</p>
        </div>
        <Switch
          lens={false}
          aria-label={t("notifications.desktop")}
          checked={controller.enabled}
          disabled={unavailable || controller.requesting}
          onCheckedChange={(checked) => {
            if (checked) void controller.enable();
            else controller.disable();
          }}
        />
      </div>
      <p className="notification-permission" role="status">
        {t(
          !controller.supported
            ? "notifications.unsupported"
            : !controller.secure
              ? "notifications.insecure"
              : controller.permission === "denied"
                ? "notifications.denied"
                : controller.runtimeError
                  ? "notifications.unavailable"
                  : controller.enabled
                    ? "notifications.on"
                    : controller.permission === "granted"
                      ? "notifications.off"
                      : "notifications.ask",
        )}
      </p>
      <p className="form-note">{t("notifications.limit")}</p>
      <div className="modal-actions">
        <button className="secondary-button lg-interactive" type="button" onClick={onClose}>
          {t("common.close")}
        </button>
      </div>
    </Modal>
  );
}
