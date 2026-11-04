import type { MessageReference } from './api/client';
import { t } from './i18n';

export function MessageReferenceQuote({ reference, chatName }: { reference: MessageReference; chatName?: string }) {
  return <div className="source-quote message-reference">
    <strong>{t('chat.referenceFrom', { name: reference.author_name })}{chatName && chatName !== reference.author_name && ' · ' + chatName}</strong>
    {reference.body && <span>{reference.body.length > 320 ? reference.body.slice(0, 320) + '…' : reference.body}</span>}
    {!!reference.attachments.length && <span>{reference.attachments.map(file => file.name).join(' · ')}</span>}
  </div>;
}
