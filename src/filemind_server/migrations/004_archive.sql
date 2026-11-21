CREATE TABLE archive_preferences (
 user_id TEXT PRIMARY KEY REFERENCES users(id), auto_archive INTEGER NOT NULL DEFAULT 0,
 ai_mode TEXT NOT NULL DEFAULT 'off', rules TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL
);
CREATE TABLE archive_jobs (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 source_transfer_id TEXT UNIQUE REFERENCES transfer_sessions(id), idempotency_key TEXT NOT NULL,
 filename TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
 original_category TEXT NOT NULL DEFAULT '未分类', state TEXT NOT NULL,
 primary_owner TEXT, backup_owner TEXT, upload_owner TEXT, lease_token TEXT,
 lease_expires_at REAL, worker_token TEXT, worker_expires_at REAL,
 plan_json TEXT, storage_key TEXT, model TEXT, error TEXT NOT NULL DEFAULT '',
 retry_count INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
 created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL NOT NULL, completed_at REAL,
 UNIQUE(user_id,idempotency_key)
);
CREATE INDEX archive_ready ON archive_jobs(state,retry_at,worker_expires_at);
CREATE TABLE ingest_sessions (
 job_id TEXT PRIMARY KEY REFERENCES archive_jobs(id), offset INTEGER NOT NULL DEFAULT 0,
 reserved_bytes INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'OPEN', expires_at REAL NOT NULL
);
CREATE TABLE ingest_chunks (
 job_id TEXT NOT NULL REFERENCES archive_jobs(id), offset INTEGER NOT NULL,
 size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(job_id,offset)
);
CREATE TABLE archive_events (
 id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES archive_jobs(id),
 state TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
CREATE TABLE storage_objects (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES archive_jobs(id),
 provider TEXT NOT NULL, object_key TEXT NOT NULL UNIQUE, size_bytes INTEGER NOT NULL,
 sha256 TEXT NOT NULL, verification_level TEXT NOT NULL, verified_at REAL NOT NULL
);
CREATE TABLE global_files (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), visibility TEXT NOT NULL DEFAULT 'private',
 created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE file_versions (
 id TEXT PRIMARY KEY, file_id TEXT NOT NULL REFERENCES global_files(id),
 version INTEGER NOT NULL, object_id TEXT NOT NULL REFERENCES storage_objects(id),
 filename TEXT NOT NULL, category TEXT NOT NULL, target_directory TEXT NOT NULL,
 summary TEXT NOT NULL, sha256 TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(file_id,version)
);
CREATE TABLE file_sources (
 version_id TEXT PRIMARY KEY REFERENCES file_versions(id), transfer_id TEXT REFERENCES transfer_sessions(id),
 primary_owner TEXT, backup_owner TEXT
);
CREATE TABLE catalog_revisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL REFERENCES users(id),
 job_id TEXT NOT NULL UNIQUE REFERENCES archive_jobs(id), created_at REAL NOT NULL
);
CREATE TABLE catalog_entries (
 file_id TEXT PRIMARY KEY REFERENCES global_files(id), version_id TEXT NOT NULL REFERENCES file_versions(id),
 revision_id INTEGER NOT NULL REFERENCES catalog_revisions(id)
);
CREATE TABLE outbox_events (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES archive_jobs(id),
 kind TEXT NOT NULL, published_at REAL, created_at REAL NOT NULL
);
CREATE TABLE reconciliation_jobs (
 id INTEGER PRIMARY KEY, created_at REAL NOT NULL, result_json TEXT NOT NULL
);
