-- D1 schema for the click counter. Run: wrangler d1 execute ig_clicks --file schema.sql
CREATE TABLE IF NOT EXISTS clicks (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  utm_content TEXT NOT NULL,
  ts          INTEGER NOT NULL,
  ua          TEXT
);
CREATE INDEX IF NOT EXISTS idx_clicks_utm ON clicks (utm_content);
