ALTER TABLE catalog_sync_cursors ADD COLUMN sent_revision INTEGER NOT NULL DEFAULT 0;
UPDATE catalog_sync_cursors SET sent_revision=revision,revision=0;
CREATE TABLE catalog_sync_deliveries (
 receipt_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 client_id TEXT NOT NULL, revision INTEGER NOT NULL, acl TEXT NOT NULL,
 expires_at REAL NOT NULL
);
CREATE TABLE storage_read_checks (
 object_key TEXT PRIMARY KEY, signature TEXT NOT NULL, sha256 TEXT NOT NULL,
 verified_at REAL NOT NULL
);
