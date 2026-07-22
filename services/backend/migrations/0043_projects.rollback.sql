-- Rollback for 0043_projects.sql (drop children before parents).

DROP TABLE IF EXISTS project_templates;
DROP TABLE IF EXISTS project_audit_events;
DROP TABLE IF EXISTS project_activity_counts;
DROP TABLE IF EXISTS project_activity;
DROP TABLE IF EXISTS project_stars;
DROP TABLE IF EXISTS project_memberships;
DROP TABLE IF EXISTS projects;
