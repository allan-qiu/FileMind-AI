CREATE TABLE archive_storage_bindings (
 job_id TEXT PRIMARY KEY REFERENCES archive_jobs(id), provider TEXT NOT NULL
);
INSERT INTO archive_storage_bindings SELECT id,'local' FROM archive_jobs WHERE storage_key IS NOT NULL;
CREATE TABLE ai_request_inputs (
 usage_id TEXT PRIMARY KEY REFERENCES ai_usage_records(id), request_hash TEXT NOT NULL,
 cache_source_id TEXT REFERENCES ai_usage_records(id)
);
CREATE INDEX ai_request_hash ON ai_request_inputs(request_hash);
