-- Additive reference DDL. app.init_db detects the old bounded type and applies
-- this once; no attachment row or filesystem path is rewritten.
ALTER TABLE tt_chat_attachments ALTER COLUMN relative_path TYPE TEXT;
