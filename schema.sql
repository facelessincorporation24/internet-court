CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plaintiff TEXT NOT NULL,
    defendant TEXT NOT NULL,
    charge TEXT NOT NULL,
    evidence TEXT NOT NULL,
    guilty_votes INTEGER DEFAULT 0,
    not_guilty_votes INTEGER DEFAULT 0,
    life_sentence_votes INTEGER DEFAULT 0,
    react_lol INTEGER DEFAULT 0,
    react_rage INTEGER DEFAULT 0,
    react_shock INTEGER DEFAULT 0,
    react_salute INTEGER DEFAULT 0,
    status TEXT DEFAULT 'open',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);