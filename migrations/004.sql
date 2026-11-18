CREATE TABLE IF NOT EXISTS autopilot_attempt (
  vacancy_id TEXT PRIMARY KEY REFERENCES vacancy(id),
  source TEXT NOT NULL,
  state TEXT NOT NULL,
  detail TEXT NOT NULL,
  reference TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_autopilot_attempt_status ON autopilot_attempt(state, updated_at);
