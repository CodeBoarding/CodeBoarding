-- A later migration that mentions the schema; it sorts first but declares nothing.
ALTER TABLE vets ADD COLUMN specialty VARCHAR(80);
