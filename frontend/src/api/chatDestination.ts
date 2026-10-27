import type { ApiClient, Bootstrap, Chat } from './client';
export type DuetNavigationTarget = { chatId: string; token: string };
export type ChatDestination = { chatId: string; duetChatId?: string };
/** Links, reminders, and notifications resolve unknown IDs before selecting a main chat. */
export async function resolveChatDestination(api: ApiClient, chats: Chat[], chatId: string): Promise<ChatDestination> {
  const target = chats.find(chat => chat.id === chatId) || await api.get<Chat>(`/chats/${encodeURIComponent(chatId)}/summary`);
  if (target.kind !== 'duet') return { chatId: target.id };
  const ownPrivate = chats.find(chat => chat.kind === 'private' && chat.member_owner_ids.includes(api.ownerId)) ||
    (await api.get<Bootstrap>('/bootstrap')).chats.find(chat => chat.kind === 'private' && chat.member_owner_ids.includes(api.ownerId));
  if (!ownPrivate) throw new Error('Your private Duet conversation is unavailable.');
  return { chatId: ownPrivate.id, duetChatId: target.id };
}
