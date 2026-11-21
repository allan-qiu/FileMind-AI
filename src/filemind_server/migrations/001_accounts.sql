CREATE TABLE users (
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('user','workspace_admin','system_admin')),
 enabled INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL,
 quota_bytes INTEGER NOT NULL DEFAULT 1073741824 CHECK(quota_bytes >= 0)
);
CREATE TABLE workspaces (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_id TEXT NOT NULL REFERENCES users(id),
 created_at INTEGER NOT NULL
);
CREATE TABLE workspace_members (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), user_id TEXT NOT NULL REFERENCES users(id),
 permission TEXT NOT NULL CHECK(permission IN ('admin','editor','viewer')),
 PRIMARY KEY(workspace_id,user_id)
);
CREATE TABLE devices (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL,
 platform TEXT NOT NULL CHECK(platform IN ('windows','browser','android','ios')),
 created_at INTEGER NOT NULL, last_seen INTEGER, revoked_at INTEGER
);
CREATE TABLE auth_sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 scope TEXT NOT NULL CHECK(scope IN ('user','admin','device')),
 device_id TEXT REFERENCES devices(id), csrf_token TEXT NOT NULL,
 created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX auth_sessions_user ON auth_sessions(user_id);
CREATE TABLE audit_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id TEXT REFERENCES users(id),
 action TEXT NOT NULL, target_id TEXT, detail TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE login_attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL, started_at INTEGER NOT NULL);
CREATE TABLE platform_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
