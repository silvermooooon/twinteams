-- Add forum metadata without publishing pending drafts or rewriting historical work.
ALTER TABLE tt_messages ADD COLUMN IF NOT EXISTS mention_owner_ids JSON NOT NULL DEFAULT '[]';
ALTER TABLE tt_messages ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE tt_messages ADD COLUMN IF NOT EXISTS edited_at TIMESTAMPTZ;
ALTER TABLE tt_duet_conversations ADD COLUMN IF NOT EXISTS opening_mention_owner_ids JSON NOT NULL DEFAULT '[]';
ALTER TABLE tt_duet_conversations ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1;
CREATE TABLE IF NOT EXISTS tt_duet_post_revisions (
    message_id VARCHAR(80) NOT NULL REFERENCES tt_messages(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    body TEXT NOT NULL,
    mention_owner_ids JSON NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (message_id, revision)
);
ALTER TABLE tt_chat_attachments ADD COLUMN IF NOT EXISTS granted_owner_ids JSON;
UPDATE tt_chat_attachments AS attachment
SET granted_owner_ids = (
    SELECT json_agg(audience.owner_id ORDER BY audience.owner_id)
    FROM (
        SELECT member.owner_id FROM tt_memberships AS member
        WHERE member.chat_id = attachment.chat_id AND member.left_at IS NULL
        UNION SELECT attachment.owner_id
    ) AS audience
)
FROM tt_chats AS chat
WHERE chat.id = attachment.chat_id AND chat.kind = 'duet' AND (attachment.granted_owner_ids IS NULL OR attachment.granted_owner_ids::jsonb = 'null'::jsonb);
