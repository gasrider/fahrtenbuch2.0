CREATE TABLE hu_corrections (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username TEXT NOT NULL REFERENCES users(username),
    fahrzeug_id BIGINT,
    datum DATE,
    km_at_hu INTEGER,
    werkstattort TEXT,
    stopps_vor_hu TEXT DEFAULT '',
    stopps_nach_hu TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT now()
);
-- Nur der User darf seine eigenen HU-Daten sehen/bearbeiten
ALTER TABLE hu_corrections ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Users see own hu_corrections" ON hu_corrections
    FOR ALL USING (username = current_user);
