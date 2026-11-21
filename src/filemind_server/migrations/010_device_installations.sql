CREATE TABLE device_installations (
 user_id TEXT NOT NULL REFERENCES users(id), installation_id TEXT NOT NULL,
 device_id TEXT NOT NULL REFERENCES devices(id), PRIMARY KEY(user_id,installation_id)
);
