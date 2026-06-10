CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plaintiff TEXT NOT NULL,
    defendant TEXT NOT NULL,
    charge TEXT NOT NULL,
    evidence TEXT NOT NULL,
    category TEXT DEFAULT 'other',
    guilty_votes INTEGER DEFAULT 0,
    not_guilty_votes INTEGER DEFAULT 0,
    life_sentence_votes INTEGER DEFAULT 0,
    react_lol INTEGER DEFAULT 0,
    react_rage INTEGER DEFAULT 0,
    react_shock INTEGER DEFAULT 0,
    react_salute INTEGER DEFAULT 0,
    defendant_reply TEXT DEFAULT NULL,
    status TEXT DEFAULT 'open',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS case_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (case_id) REFERENCES cases(id)
);