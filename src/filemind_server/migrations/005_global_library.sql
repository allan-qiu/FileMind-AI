CREATE TABLE file_metadata (
 file_id TEXT PRIMARY KEY REFERENCES global_files(id), filename TEXT NOT NULL,
 category TEXT NOT NULL, directory TEXT NOT NULL, favorite INTEGER NOT NULL DEFAULT 0,
 deleted_at REAL, deleted_by TEXT REFERENCES users(id), workspace_id TEXT REFERENCES workspaces(id),
 revision INTEGER NOT NULL DEFAULT 0
);
INSERT INTO file_metadata(file_id,filename,category,directory)
 SELECT c.file_id,v.filename,v.category,v.target_directory FROM catalog_entries c
 JOIN file_versions v ON v.id=c.version_id;
CREATE TABLE library_folders (
 scope TEXT NOT NULL, path TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(scope,path)
);
CREATE TABLE library_acl_epoch (id INTEGER PRIMARY KEY CHECK(id=1), epoch INTEGER NOT NULL);
INSERT INTO library_acl_epoch VALUES (1,1);
CREATE TABLE catalog_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, file_id TEXT NOT NULL REFERENCES global_files(id),
 kind TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX catalog_events_scope ON catalog_events(scope,id);
INSERT INTO catalog_events(scope,file_id,kind,payload,created_at)
 SELECT 'user:'||f.user_id,c.file_id,'MIGRATED','{}',f.updated_at
 FROM catalog_entries c JOIN global_files f ON f.id=c.file_id ORDER BY c.revision_id;
UPDATE file_metadata SET revision=(SELECT max(id) FROM catalog_events e WHERE e.file_id=file_metadata.file_id);
CREATE TABLE catalog_sync_cursors (
 user_id TEXT NOT NULL REFERENCES users(id), client_id TEXT NOT NULL,
 revision INTEGER NOT NULL, acl TEXT NOT NULL, updated_at REAL NOT NULL,
 PRIMARY KEY(user_id,client_id)
);
CREATE TABLE library_version_requests (
 job_id TEXT PRIMARY KEY REFERENCES archive_jobs(id), file_id TEXT NOT NULL REFERENCES global_files(id),
 base_version_id TEXT NOT NULL REFERENCES file_versions(id), actor_id TEXT NOT NULL REFERENCES users(id),
 device_id TEXT REFERENCES devices(id), request_hash TEXT NOT NULL
);
CREATE TABLE file_version_sources (
 version_id TEXT PRIMARY KEY REFERENCES file_versions(id), actor_id TEXT REFERENCES users(id),
 device_id TEXT REFERENCES devices(id), base_version_id TEXT REFERENCES file_versions(id),
 job_id TEXT REFERENCES archive_jobs(id)
);
CREATE TABLE file_conflicts (
 id TEXT PRIMARY KEY, file_id TEXT NOT NULL REFERENCES global_files(id),
 base_version_id TEXT NOT NULL REFERENCES file_versions(id), current_version_id TEXT NOT NULL REFERENCES file_versions(id),
 candidate_version_id TEXT NOT NULL UNIQUE REFERENCES file_versions(id), actor_id TEXT NOT NULL REFERENCES users(id),
 device_id TEXT REFERENCES devices(id), reason TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'OPEN',
 created_at REAL NOT NULL, resolved_at REAL, resolution TEXT
);
CREATE TABLE file_tombstones (
 file_id TEXT PRIMARY KEY REFERENCES global_files(id), actor_id TEXT NOT NULL REFERENCES users(id),
 deleted_at REAL NOT NULL, directory TEXT NOT NULL, version_id TEXT NOT NULL REFERENCES file_versions(id)
);
CREATE TABLE file_operation_events (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), file_id TEXT NOT NULL REFERENCES global_files(id),
 idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, kind TEXT NOT NULL,
 before_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at REAL NOT NULL,
 UNIQUE(user_id,idempotency_key)
);
CREATE TABLE file_restore_records (
 id TEXT PRIMARY KEY, file_id TEXT NOT NULL REFERENCES global_files(id), actor_id TEXT NOT NULL REFERENCES users(id),
 kind TEXT NOT NULL, version_id TEXT REFERENCES file_versions(id), created_at REAL NOT NULL
);
CREATE TABLE library_download_tasks (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), device_id TEXT REFERENCES devices(id),
 version_id TEXT NOT NULL REFERENCES file_versions(id), state TEXT NOT NULL, bytes_sent INTEGER NOT NULL DEFAULT 0,
 requested_bytes INTEGER NOT NULL, error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX file_metadata_directory ON file_metadata(directory,deleted_at);
CREATE INDEX versions_file ON file_versions(file_id,version);
