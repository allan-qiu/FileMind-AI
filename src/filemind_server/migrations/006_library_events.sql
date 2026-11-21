ALTER TABLE catalog_events RENAME TO catalog_events_v5;
CREATE TABLE catalog_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, file_id TEXT REFERENCES global_files(id),
 kind TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL
);
INSERT INTO catalog_events SELECT * FROM catalog_events_v5;
DROP TABLE catalog_events_v5;
CREATE INDEX catalog_events_scope ON catalog_events(scope,id);
