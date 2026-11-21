CREATE TABLE device_receivers (
 device_id TEXT PRIMARY KEY REFERENCES devices(id), ready_at REAL NOT NULL
);
CREATE TABLE transfer_sessions (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 sender_id TEXT NOT NULL REFERENCES devices(id), receiver_id TEXT NOT NULL REFERENCES devices(id),
 filename TEXT NOT NULL, size_bytes INTEGER NOT NULL, expected_hash TEXT NOT NULL,
 request_key TEXT NOT NULL, state TEXT NOT NULL, bytes_received INTEGER NOT NULL DEFAULT 0,
 bytes_forwarded INTEGER NOT NULL DEFAULT 0, next_seq INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL NOT NULL,
 completed_at REAL, error TEXT NOT NULL DEFAULT '', confirmation TEXT,
 UNIQUE(sender_id,request_key)
);
CREATE INDEX transfer_participants ON transfer_sessions(user_id,created_at);
CREATE TABLE transfer_events (
 id INTEGER PRIMARY KEY, transfer_id TEXT NOT NULL REFERENCES transfer_sessions(id),
 state TEXT NOT NULL, message TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE transfer_archive_handoff (
 transfer_id TEXT PRIMARY KEY REFERENCES transfer_sessions(id), receiver_id TEXT NOT NULL,
 fallback_sender_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'NOT_IMPLEMENTED', created_at REAL NOT NULL
);
