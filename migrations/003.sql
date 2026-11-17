CREATE TABLE IF NOT EXISTS employer_sponsor (
 employer TEXT NOT NULL REFERENCES employer(name),
 country TEXT NOT NULL,
 legal_name TEXT NOT NULL,
 source TEXT NOT NULL,
 route TEXT NOT NULL,
 detail TEXT NOT NULL,
 verified TEXT NOT NULL,
 PRIMARY KEY (employer,country)
);
