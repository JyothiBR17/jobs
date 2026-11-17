CREATE TABLE IF NOT EXISTS candidate (id INTEGER PRIMARY KEY, profile TEXT NOT NULL, resume_key TEXT);
CREATE TABLE IF NOT EXISTS employer (name TEXT PRIMARY KEY, excluded INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS vacancy (
 id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL, url TEXT UNIQUE NOT NULL,
 employer TEXT NOT NULL REFERENCES employer(name), title TEXT NOT NULL, country TEXT NOT NULL,
 location TEXT NOT NULL, description TEXT NOT NULL, discovered TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, UNIQUE(source, source_id)
);
CREATE TABLE IF NOT EXISTS sponsorship (
 vacancy_id TEXT PRIMARY KEY REFERENCES vacancy(id), status TEXT NOT NULL, evidence TEXT NOT NULL,
 source TEXT NOT NULL, verified TEXT NOT NULL, route TEXT, india_considered INTEGER, relocation INTEGER,
 reviewer TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS matching (
 vacancy_id TEXT PRIMARY KEY REFERENCES vacancy(id), score INTEGER NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS draft (
 vacancy_id TEXT PRIMARY KEY REFERENCES vacancy(id), cv TEXT NOT NULL, letter TEXT NOT NULL,
 answers TEXT NOT NULL, unresolved TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS approval (
 vacancy_id TEXT NOT NULL REFERENCES draft(vacancy_id), revision INTEGER NOT NULL,
 actor TEXT NOT NULL, decision TEXT NOT NULL, time TEXT NOT NULL, PRIMARY KEY(vacancy_id, revision)
);
CREATE TABLE IF NOT EXISTS submission (
 vacancy_id TEXT PRIMARY KEY REFERENCES draft(vacancy_id), channel TEXT NOT NULL,
 reference TEXT NOT NULL, recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tracking (
 id TEXT PRIMARY KEY, vacancy_id TEXT NOT NULL REFERENCES submission(vacancy_id),
 status TEXT NOT NULL, description TEXT NOT NULL, event_time TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (id TEXT PRIMARY KEY, time TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoint (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
 id TEXT PRIMARY KEY, message TEXT NOT NULL, channel TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 available TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending'
);
