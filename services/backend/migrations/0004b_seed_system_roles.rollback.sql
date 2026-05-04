-- Rollback for 0004b_seed_system_roles. Removes only the seeded system rows;
-- per-org roles created by users are untouched.
DELETE FROM roles WHERE role_id IN (
    'role_system_admin',
    'role_system_employee',
    'role_system_auditor',
    'role_system_service'
);
