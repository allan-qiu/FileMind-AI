CREATE TABLE share_daily_traffic (
 owner_user_id TEXT NOT NULL REFERENCES users(id), utc_day INTEGER NOT NULL,
 wire_attempt_bytes INTEGER NOT NULL DEFAULT 0 CHECK(wire_attempt_bytes>=0),
 PRIMARY KEY(owner_user_id,utc_day)
);
INSERT INTO share_daily_traffic
 SELECT s.owner_user_id,CAST(e.created_at/86400 AS INTEGER),sum(e.bytes_sent)
 FROM share_events e JOIN share_links s ON s.share_id=e.share_id
 WHERE e.bytes_sent>0 GROUP BY s.owner_user_id,CAST(e.created_at/86400 AS INTEGER);
CREATE INDEX share_event_link_time ON share_events(share_id,created_at);
