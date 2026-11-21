CREATE TABLE temporary_share_objects (
 object_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL REFERENCES users(id),
 storage_location TEXT NOT NULL UNIQUE, original_filename TEXT NOT NULL,
 file_size INTEGER NOT NULL CHECK(file_size>=0), content_sha256 TEXT NOT NULL,
 created_at REAL NOT NULL, expires_at REAL NOT NULL,
 deletion_status TEXT NOT NULL DEFAULT 'LIVE', error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE share_uploads (
 upload_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL REFERENCES users(id),
 storage_id TEXT NOT NULL, filename TEXT NOT NULL, file_size INTEGER NOT NULL CHECK(file_size>=0),
 sha256 TEXT NOT NULL, offset INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
 expires_at REAL NOT NULL, created_at REAL NOT NULL, object_id TEXT REFERENCES temporary_share_objects(object_id)
);
CREATE TABLE share_links (
 share_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL REFERENCES users(id),
 workspace_id TEXT REFERENCES workspaces(id), source_type TEXT NOT NULL CHECK(source_type IN ('library','temporary')),
 file_version_id TEXT REFERENCES file_versions(id), temporary_object_id TEXT REFERENCES temporary_share_objects(object_id),
 token_hash TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
 max_downloads INTEGER NOT NULL CHECK(max_downloads>0),
 completed_downloads INTEGER NOT NULL DEFAULT 0 CHECK(completed_downloads>=0 AND completed_downloads<=max_downloads),
 expires_at REAL NOT NULL, password_hash TEXT, status TEXT NOT NULL CHECK(status IN ('ACTIVE','EXPIRED','REVOKED')),
 created_at REAL NOT NULL, updated_at REAL NOT NULL, revoked_at REAL,
 CHECK((source_type='library' AND file_version_id IS NOT NULL AND temporary_object_id IS NULL) OR
 (source_type='temporary' AND file_version_id IS NULL AND temporary_object_id IS NOT NULL))
);
CREATE TABLE share_download_sessions (
 session_id TEXT PRIMARY KEY, share_id TEXT NOT NULL REFERENCES share_links(share_id),
 session_token_hash TEXT NOT NULL UNIQUE, reserved_quota INTEGER NOT NULL CHECK(reserved_quota IN (0,1)),
 transferred_bytes INTEGER NOT NULL DEFAULT 0 CHECK(transferred_bytes>=0), expected_bytes INTEGER NOT NULL CHECK(expected_bytes>=0),
 reservation_expires_at REAL NOT NULL, status TEXT NOT NULL CHECK(status IN ('RESERVED','TRANSFERRING','COMPLETED','FAILED','CANCELLED','EXPIRED')),
 created_at REAL NOT NULL, started_at REAL, completed_at REAL, failed_at REAL,
 client_received_at REAL, error TEXT NOT NULL DEFAULT '', request_key TEXT NOT NULL,
 UNIQUE(share_id,request_key)
);
CREATE TABLE share_events (
 event_id INTEGER PRIMARY KEY AUTOINCREMENT, share_id TEXT REFERENCES share_links(share_id),
 kind TEXT NOT NULL, actor_id TEXT, session_id TEXT, bytes_sent INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
CREATE TABLE share_access_grants (
 grant_hash TEXT PRIMARY KEY, share_id TEXT NOT NULL REFERENCES share_links(share_id), expires_at REAL NOT NULL
);
CREATE TABLE share_rate_limits (key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires_at REAL NOT NULL);
CREATE INDEX share_owner ON share_links(owner_user_id,created_at);
CREATE INDEX share_temporary_refs ON share_links(temporary_object_id,status,expires_at);
CREATE INDEX share_session_quota ON share_download_sessions(share_id,status,reservation_expires_at);
CREATE INDEX share_event_time ON share_events(created_at);
