-- Rollback for 0026_user_profile_bio.sql.

ALTER TABLE user_profiles DROP COLUMN IF EXISTS bio;
