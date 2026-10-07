"""User, role and permission administration (the existing 001 authorization tables; no schema change).

Every write runs on the caller's transaction. Password hashes and PIN hashes are NEVER selected by a read
model: the only hash this repository touches is the one it replaces (inherited `replace_password_hash`).
Role assignments and role grants are configuration (not ledger history); each change is audited by the service
with before / after values.
"""
from repositories.authentication_repository import AuthenticationRepository


class UserAdminRepository(AuthenticationRepository):

    # ------------------------------------------------------------ read models (no hashes)
    def users(self):
        return self._fetch_all(
            "SELECT u.id, u.username, u.display_name, u.is_active, u.must_change_password, u.last_login_at, "
            "u.employee_id, e.name AS employee_name, e.is_active AS employee_active "
            "FROM users u LEFT JOIN employees e ON e.id = u.employee_id ORDER BY u.id")

    def user_roles(self):
        return self._fetch_all(
            "SELECT ur.user_id, r.id AS role_id, r.code, r.name, r.is_active "
            "FROM user_roles ur JOIN roles r ON r.id = ur.role_id ORDER BY ur.user_id, r.id")

    def roles(self):
        return self._fetch_all(
            "SELECT r.id, r.code, r.name, r.is_system, r.is_active, "
            "(SELECT COUNT(*) FROM user_roles ur WHERE ur.role_id = r.id) AS user_count "
            "FROM roles r ORDER BY r.id")

    def role_grants(self):
        return self._fetch_all(
            "SELECT rp.role_id, p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
            "WHERE p.is_active = TRUE ORDER BY rp.role_id, p.code")

    def permissions(self):
        return self._fetch_all(
            "SELECT id, code, name, description FROM permissions WHERE is_active = TRUE ORDER BY code")

    def employees_without_user(self):
        return self._fetch_all(
            "SELECT e.id, e.employee_code, e.name FROM employees e WHERE e.is_active = TRUE "
            "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.employee_id = e.id) ORDER BY e.id")

    # ------------------------------------------------------------ locked reads for writes
    def lock_user(self, user_id):
        return self._fetch_one(
            "SELECT id, username, display_name, is_active, employee_id FROM users WHERE id = %s FOR UPDATE",
            (int(user_id),))

    def lock_role_by_code(self, code):
        return self._fetch_one(
            "SELECT id, code, name, is_system, is_active FROM roles WHERE code = %s FOR UPDATE", (code,))

    def permission_by_code(self, code):
        return self._fetch_one("SELECT id, code, is_active FROM permissions WHERE code = %s", (code,))

    def role_codes_of(self, user_id, *, lock=False):
        rows = self._fetch_all(
            "SELECT r.code FROM user_roles ur JOIN roles r ON r.id = ur.role_id WHERE ur.user_id = %s ORDER BY r.code"
            + (" FOR UPDATE" if lock else ""), (int(user_id),))
        return [row["code"] for row in rows]

    def active_owner_ids(self, *, lock=True):
        """Active users who currently hold an ACTIVE OWNER role that still grants APP_LOGIN (locked: two
        administrators can never remove the last two owners at the same time)."""
        rows = self._fetch_all(
            "SELECT DISTINCT u.id FROM users u "
            "JOIN user_roles ur ON ur.user_id = u.id JOIN roles r ON r.id = ur.role_id AND r.code = 'OWNER' "
            "AND r.is_active = TRUE "
            "JOIN role_permissions rp ON rp.role_id = r.id JOIN permissions p ON p.id = rp.permission_id "
            "AND p.code = 'APP_LOGIN' AND p.is_active = TRUE "
            "WHERE u.is_active = TRUE" + (" FOR UPDATE" if lock else ""))
        return {int(row["id"]) for row in rows}

    def username_exists(self, username):
        return self._fetch_one("SELECT id FROM users WHERE username = %s FOR UPDATE", (username,)) is not None

    def employee_state(self, employee_id):
        return self._fetch_one(
            "SELECT e.id, e.is_active, EXISTS(SELECT 1 FROM users u WHERE u.employee_id = e.id) AS has_user "
            "FROM employees e WHERE e.id = %s FOR UPDATE", (int(employee_id),))

    # ------------------------------------------------------------ writes
    def set_user_active(self, user_id, active):
        self._execute("UPDATE users SET is_active = %s WHERE id = %s", (bool(active), int(user_id)))

    def replace_user_roles(self, user_id, role_ids):
        self._execute("DELETE FROM user_roles WHERE user_id = %s", (int(user_id),))
        for role_id in role_ids:
            self._execute("INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)", (int(user_id), int(role_id)))

    def grant(self, role_id, permission_id):
        self._execute("INSERT IGNORE INTO role_permissions (role_id, permission_id) VALUES (%s, %s)",
                      (int(role_id), int(permission_id)))

    def revoke(self, role_id, permission_id):
        self._execute("DELETE FROM role_permissions WHERE role_id = %s AND permission_id = %s",
                      (int(role_id), int(permission_id)))

    def set_password(self, user_id, encoded):
        """Replace the Argon2id hash; must_change_password stays FALSE (there is no self-service change yet)."""
        self._execute("UPDATE users SET password_hash = %s, must_change_password = FALSE WHERE id = %s",
                      (encoded, int(user_id)))

    def create_user(self, username, display_name, encoded, employee_id):
        return self._execute(
            "INSERT INTO users (employee_id, username, password_hash, display_name, is_active, must_change_password) "
            "VALUES (%s, %s, %s, %s, TRUE, FALSE)", (employee_id, username, encoded, display_name))
