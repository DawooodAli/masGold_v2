"""Private credential reads and parameterized identity/RBAC queries."""
from contextlib import contextmanager

from mysql.connector.connection import MySQLConnection

from config.database_config import DatabaseConfig
from repositories.base_repository import BaseRepository


@contextmanager
def authentication_transaction():
    config = DatabaseConfig.from_env()
    connection = MySQLConnection(
        host=config.host, port=config.port, database=config.database,
        user=config.user, password=config.password, autocommit=False,
        connection_timeout=5, read_timeout=5, write_timeout=5,
    )
    try:
        connection.start_transaction(isolation_level="READ COMMITTED")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


class AuthenticationRepository(BaseRepository):
    def find_credentials(self, username):
        return self._fetch_one(
            "SELECT id, password_hash FROM users WHERE username = %s", (username,))

    def get_identity(self, user_id, *, lock=False):
        user = self._fetch_one(
            "SELECT id, employee_id, display_name, is_active, must_change_password, "
            "password_hash FROM users WHERE id = %s" + (" FOR UPDATE" if lock else ""),
            (user_id,),
        )
        if user and user["employee_id"] is not None:
            employee = self._fetch_one(
                "SELECT is_active FROM employees WHERE id = %s" +
                (" FOR UPDATE" if lock else ""), (user["employee_id"],))
            user["employee_active"] = bool(employee and employee["is_active"])
        return user

    def effective_permissions(self, user_id, *, lock=False):
        # Fetch rows rather than GROUP BY so protected operations can lock grants.
        rows = self._fetch_all(
            "SELECT p.code FROM user_roles ur "
            "JOIN roles r ON r.id = ur.role_id "
            "JOIN role_permissions rp ON rp.role_id = r.id "
            "JOIN permissions p ON p.id = rp.permission_id "
            "WHERE ur.user_id = %s AND r.is_active = TRUE AND p.is_active = TRUE"
            + (" FOR UPDATE" if lock else ""), (user_id,))
        return frozenset(row["code"] for row in rows)

    def _execute(self, sql, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            return cursor.lastrowid
        finally:
            cursor.close()

    def record_login(self, user_id, replacement_hash=None):
        if replacement_hash is None:
            self._execute("UPDATE users SET last_login_at = NOW() WHERE id = %s", (user_id,))
        else:
            self._execute("UPDATE users SET last_login_at = NOW(), password_hash = %s "
                          "WHERE id = %s", (replacement_hash, user_id))

    def acquire_bootstrap_lock(self):
        row = self._fetch_one(
            "SELECT GET_LOCK(CONCAT(DATABASE(), ':auth_bootstrap'), 5) AS acquired")
        if not row or row["acquired"] != 1:
            raise ValueError("تعذر حجز تهيئة المستخدم الأول.")
        # The dedicated connection owns this lock until it closes after COMMIT.

    def has_users(self):
        return self._fetch_one("SELECT id FROM users LIMIT 1") is not None

    def bootstrap_owner_role(self):
        role = self._fetch_one(
            "SELECT id FROM roles WHERE code = 'OWNER' AND is_active = TRUE FOR UPDATE")
        if not role:
            raise ValueError("دور OWNER النشط غير موجود.")
        permission = self._fetch_one(
            "SELECT p.id FROM role_permissions rp JOIN permissions p "
            "ON p.id = rp.permission_id WHERE rp.role_id = %s "
            "AND p.code = 'APP_LOGIN' AND p.is_active = TRUE FOR UPDATE", (role["id"],))
        if not permission:
            raise ValueError("دور OWNER لا يمنح صلاحية APP_LOGIN النشطة.")
        return role["id"]

    def create_first_user(self, username, display_name, encoded, employee_id, role_id):
        if employee_id is not None:
            employee = self._fetch_one(
                "SELECT id FROM employees WHERE id = %s AND is_active = TRUE FOR UPDATE",
                (employee_id,))
            if not employee:
                raise ValueError("الموظف المحدد غير موجود أو غير نشط.")
        user_id = self._execute(
            "INSERT INTO users (username, display_name, password_hash, employee_id, "
            "is_active, must_change_password) VALUES (%s, %s, %s, %s, TRUE, FALSE)",
            (username, display_name, encoded, employee_id))
        self._execute("INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
                      (user_id, role_id))
        return user_id
