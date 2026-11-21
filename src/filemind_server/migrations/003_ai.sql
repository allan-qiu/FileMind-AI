CREATE TABLE ai_usage_records (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 provider TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL,
 input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
 estimated_cost REAL NOT NULL DEFAULT 0, reserved_cost REAL NOT NULL DEFAULT 0,
 currency TEXT NOT NULL, price_version TEXT NOT NULL, pricing_json TEXT NOT NULL,
 simulated INTEGER NOT NULL, result_json TEXT, error TEXT NOT NULL DEFAULT '',
 created_at REAL NOT NULL, finished_at REAL
);
CREATE INDEX ai_usage_month ON ai_usage_records(created_at,currency);
