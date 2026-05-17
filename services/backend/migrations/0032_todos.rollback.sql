-- Rollback for 0032_todos.sql (Phase 3 Todos destination).

DROP TABLE IF EXISTS todo_audit_events;
DROP TABLE IF EXISTS todo_series;
DROP TABLE IF EXISTS todos;
