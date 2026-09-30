"""Authentication tests use mocks/in-memory fixtures; never a live database."""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from dataclasses import replace
from io import StringIO
import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from argon2 import PasswordHasher, Type

from app.session import AppSession
from repositories.authentication_repository import AuthenticationRepository
from services.authentication_service import (
    AuthenticationService, AuthenticationError, AuthenticationResult,
    GENERIC_ERROR, CHANGE_REQUIRED,
    SetupAlreadyCompleted,
)
from services.authorization_service import AuthorizationService
from services.password_service import PasswordService
from tools.bootstrap_owner import bootstrap_owner


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.no_db = patch('database.connection.DatabaseConnection.connect',
                           side_effect=AssertionError('Live database forbidden'))
        self.no_db.start()
        self.addCleanup(self.no_db.stop)
        self.passwords = PasswordService(PasswordHasher(time_cost=1, memory_cost=1024,
                                                       parallelism=1, type=Type.ID))
        self.password = 'test-only-long-password'
        self.encoded = self.passwords.hash(self.password)
        self.user = dict(id=9007199254740993, employee_id=None, display_name='مستخدم',
                         is_active=True, must_change_password=False, password_hash=self.encoded)
        self.repo = MagicMock()
        self.repo.find_credentials.return_value = dict(id=self.user['id'], password_hash=self.encoded)
        self.repo.get_identity.side_effect = lambda *a, **kw: dict(self.user)
        self.repo.effective_permissions.return_value = frozenset({'APP_LOGIN', 'SETTINGS_MANAGE'})
        self.commits = self.rollbacks = 0
        self.now = 0

        @contextmanager
        def transaction():
            try:
                yield object()
                self.commits += 1
            except BaseException:
                self.rollbacks += 1
                raise
        self.transaction = transaction
        self.service = AuthenticationService(transaction, lambda c: self.repo,
                                             self.passwords, lambda: self.now)

    def login(self):
        return self.service.authenticate('test-user', self.password)

    def assert_denied(self, message=GENERIC_ERROR):
        with self.assertRaises(AuthenticationError) as result:
            self.login()
        self.assertEqual(str(result.exception), message)
        self.repo.record_login.assert_not_called()

    def test_valid_login_and_no_employee_link(self):
        result = self.login()
        self.assertEqual(result.user_id, self.user['id'])
        self.assertIsNone(result.employee_id)
        self.assertNotIn('password', repr(result))
        self.repo.record_login.assert_called_once_with(self.user['id'], None)

    def test_wrong_password(self):
        self.password = 'incorrect'
        self.assert_denied()

    def test_verify_user_password_reauthenticates_the_logged_in_user(self):
        """Sensitive actions (Dead Cash) re-check the actor's CURRENT password against its Argon2 hash."""
        user_id = self.user['id']
        self.assertTrue(self.service.verify_user_password(user_id, self.password))
        self.repo.record_login.assert_not_called()                         # not a login
        for bad in ('incorrect', '', None):
            self.now += 100                                                 # past any cooldown
            with self.assertRaises(AuthenticationError) as denied:
                self.service.verify_user_password(user_id, bad)
            self.assertEqual(str(denied.exception), GENERIC_ERROR)
        self.now += 100
        with self.assertRaises(AuthenticationError):                        # an unknown user
            self.repo.get_identity.side_effect = lambda *a, **kw: None
            self.service.verify_user_password(user_id, self.password)
        self.repo.get_identity.side_effect = lambda *a, **kw: dict(self.user)
        self.now += 100
        with self.assertRaises(AuthenticationError):
            self.service.verify_user_password(True, self.password)          # a bool is not a user id
        self.now += 100
        self.assertTrue(self.service.verify_user_password(user_id, self.password))

    def test_verify_user_password_failures_trigger_the_login_cooldown(self):
        user_id = self.user['id']
        with self.assertRaises(AuthenticationError):
            self.service.verify_user_password(user_id, 'incorrect')
        with self.assertRaises(AuthenticationError):                        # even the right password waits
            self.service.verify_user_password(user_id, self.password)
        self.now += 100
        self.assertTrue(self.service.verify_user_password(user_id, self.password))

    def test_unknown_user(self):
        self.repo.find_credentials.return_value = None
        self.assert_denied()

    def test_inactive_user(self):
        self.user['is_active'] = False
        self.assert_denied()

    def test_inactive_linked_employee(self):
        self.user.update(employee_id=42, employee_active=False)
        self.assert_denied()

    def test_active_linked_employee(self):
        self.user.update(employee_id=42, employee_active=True)
        self.assertEqual(self.login().employee_id, 42)

    def test_missing_app_login(self):
        self.repo.effective_permissions.return_value = frozenset({'SETTINGS_MANAGE'})
        self.assert_denied()

    def test_must_change_password(self):
        self.user['must_change_password'] = True
        self.assert_denied(CHANGE_REQUIRED)

    def test_must_change_not_disclosed_for_wrong_password(self):
        self.user['must_change_password'] = True
        self.password = 'incorrect'
        self.assert_denied()

    def test_malformed_unsupported_plaintext_hashes(self):
        for encoded in ('$argon2id$broken', '$argon2i$broken', self.password, '', None):
            with self.subTest(encoded_type=type(encoded).__name__):
                self.assertFalse(self.passwords.verify(encoded, self.password))

    def test_password_changed_during_verification(self):
        self.user['password_hash'] = self.passwords.hash('different-password')
        self.assert_denied()

    def test_no_credential_normalization(self):
        self.service.authenticate('  أَحْمَد  ', self.password)
        self.repo.find_credentials.assert_called_once_with('  أَحْمَد  ')
        self.assertFalse(self.passwords.verify(self.encoded, self.password + ' '))

    def test_cooldown_is_process_local_and_generic(self):
        self.password = 'incorrect'
        self.assert_denied()
        calls = self.repo.find_credentials.call_count
        self.assert_denied()
        self.assertEqual(self.repo.find_credentials.call_count, calls)
        self.now = 31
        self.password = 'test-only-long-password'
        self.assertIsNotNone(self.login())

    def test_errors_do_not_log_or_expose_credentials(self):
        self.repo.find_credentials.side_effect = RuntimeError(self.password + self.encoded)
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            self.assert_denied()
        self.assertEqual(output.getvalue(), '')

    def test_session_strings_logout_and_generation(self):
        session = AppSession()
        result = self.login()
        result = replace(result, employee_id=9007199254740995)
        generation = int(session.generation)
        self.assertTrue(session._establish(result, generation))
        actor = session.require_authenticated()
        self.assertEqual(session.userId, '9007199254740993')
        self.assertEqual(session.employeeId, '9007199254740995')
        self.assertTrue(session.hasPermission('APP_LOGIN'))
        session.logout()
        self.assertFalse(session.authenticated)
        self.assertEqual(session.permissions, [])
        self.assertEqual(session.userId, '')
        self.assertFalse(session.is_current(actor))
        self.assertFalse(session._establish(result, generation))

    def test_unverified_identity_cannot_establish_session(self):
        session = AppSession()
        result = AuthenticationResult(1, None, 'fake', frozenset({'APP_LOGIN'}))
        with self.assertRaises(PermissionError):
            session._establish(result, 0)

    def test_qml_identity_properties_are_read_only(self):
        session = AppSession()
        meta = session.metaObject()
        for name in ('authenticated', 'userId', 'employeeId', 'permissions', 'generation', 'displayName'):
            self.assertFalse(meta.property(meta.indexOfProperty(name)).isWritable(), name)
        self.assertEqual(meta.indexOfMethod('_establish(QVariant,int)'), -1)

    def test_authorization_rechecks_permission_and_user(self):
        session = AppSession()
        session._establish(self.login(), 0)
        actor = session.require_authenticated()
        auth = AuthorizationService(session, lambda c: self.repo)
        auth.require_permission(object(), actor, 'SETTINGS_MANAGE')
        self.repo.effective_permissions.return_value = frozenset({'APP_LOGIN'})
        with self.assertRaises(PermissionError):
            auth.require_permission(object(), actor, 'SETTINGS_MANAGE')
        self.user['is_active'] = False
        with self.assertRaises(PermissionError):
            auth.require_permission(object(), actor, 'APP_LOGIN')

    def test_authorization_rejects_missing_employee_and_stale_actor(self):
        session = AppSession()
        session._establish(self.login(), 0)
        actor = session.require_authenticated()
        auth = AuthorizationService(session, lambda c: self.repo)
        with self.assertRaises(PermissionError):
            auth.require_permission(object(), actor, 'SETTINGS_MANAGE', require_employee=True)
        session.logout()
        with self.assertRaises(PermissionError):
            auth.require_permission(object(), actor, 'SETTINGS_MANAGE')

    def test_bootstrap_refuses_existing_users(self):
        self.repo.has_users.return_value = True
        with self.assertRaises(ValueError):
            bootstrap_owner('admin', 'Owner', self.password, transaction_factory=self.transaction,
                            repository_factory=lambda c: self.repo, passwords=self.passwords)
        self.repo.create_first_user.assert_not_called()
        self.assertEqual(self.rollbacks, 1)

    def test_bootstrap_owner_permission_failure_rolls_back(self):
        self.repo.has_users.return_value = False
        self.repo.bootstrap_owner_role.side_effect = ValueError('Missing APP_LOGIN')
        with self.assertRaises(ValueError):
            bootstrap_owner('admin', 'Owner', self.password, transaction_factory=self.transaction,
                            repository_factory=lambda c: self.repo, passwords=self.passwords)
        self.assertEqual(self.commits, 0)
        self.assertEqual(self.rollbacks, 1)

    def test_bootstrap_validates_effective_permission_before_commit(self):
        self.repo.has_users.return_value = False
        self.repo.bootstrap_owner_role.return_value = 7
        self.repo.create_first_user.return_value = 12
        self.repo.effective_permissions.return_value = frozenset()
        with self.assertRaises(ValueError):
            bootstrap_owner('admin', 'Owner', self.password, transaction_factory=self.transaction,
                            repository_factory=lambda c: self.repo, passwords=self.passwords)
        self.assertEqual(self.commits, 0)
        self.assertEqual(self.rollbacks, 1)

    def test_bootstrap_hashes_and_does_not_guess_employee(self):
        self.repo.has_users.return_value = False
        self.repo.bootstrap_owner_role.return_value = 7
        self.repo.create_first_user.return_value = 12
        self.assertEqual(bootstrap_owner('admin', 'Owner', self.password,
                         transaction_factory=self.transaction, repository_factory=lambda c: self.repo,
                         passwords=self.passwords), 12)
        args = self.repo.create_first_user.call_args.args
        self.assertTrue(args[2].startswith('$argon2id$'))
        self.assertIsNone(args[3])
        self.assertEqual(self.commits, 1)

    def test_bootstrap_repository_requires_active_owner_login_grant(self):
        repository = AuthenticationRepository(None)
        repository._fetch_one = MagicMock(side_effect=[{'id': 7}, None])
        with self.assertRaises(ValueError):
            repository.bootstrap_owner_role()
        sql = repository._fetch_one.call_args_list[1].args[0]
        self.assertIn("p.code = 'APP_LOGIN'", sql)
        self.assertIn('p.is_active = TRUE', sql)

    def test_bootstrap_creates_no_forced_password_change(self):
        repository = AuthenticationRepository(None)
        repository._execute = MagicMock(return_value=12)
        repository.create_first_user('owner', 'Owner', self.encoded, None, 7)
        sql, params = repository._execute.call_args_list[0].args
        self.assertIn('is_active, must_change_password', sql)
        self.assertIn('TRUE, FALSE', sql)
        self.assertEqual(params[2], self.encoded)

    def test_bootstrap_inactive_employee_is_rejected(self):
        repository = AuthenticationRepository(None)
        repository._fetch_one = MagicMock(return_value=None)
        repository._execute = MagicMock()
        with self.assertRaises(ValueError):
            repository.create_first_user('owner', 'Owner', self.encoded, 42, 7)
        repository._execute.assert_not_called()

    def test_bootstrap_hidden_input_fallback_refused(self):
        import getpass
        from tools.bootstrap_owner import main
        with patch('builtins.input', side_effect=['owner', 'Owner', '']), \
             patch('getpass.getpass', side_effect=getpass.GetPassWarning()), \
             patch('tools.bootstrap_owner.bootstrap_owner') as create, \
             redirect_stdout(StringIO()):
            self.assertEqual(main(), 1)
            create.assert_not_called()

    def test_first_run_count_and_active_employee_options(self):
        self.repo.user_count.return_value = 0
        self.repo.active_employees.return_value = [dict(id=42, employee_code='EMP042', name='موظف')]
        self.assertTrue(self.service.requires_first_run_setup())
        status = self.service.first_run_status()
        self.assertTrue(status['setup_required'])
        self.assertEqual(status['employees'][0]['id'], '42')
        self.repo.user_count.return_value = 1
        self.assertFalse(self.service.requires_first_run_setup())
        self.assertEqual(self.service.first_run_status(), {'setup_required': False, 'employees': []})

    def setup_first_owner(self, employee_id=None):
        self.repo.has_users.return_value = False
        self.repo.bootstrap_owner_role.return_value = 7
        self.repo.create_first_user.return_value = 12
        return self.service.create_first_owner('Owner', self.password, self.password, employee_id)

    def test_first_owner_fixed_admin_hash_and_session_after_commit(self):
        result = self.setup_first_owner()
        self.assertEqual(self.commits, 1)
        args = self.repo.create_first_user.call_args.args
        self.assertEqual(args[0], 'admin')
        self.assertTrue(args[2].startswith('$argon2id$'))
        self.assertTrue(self.passwords.verify(args[2], self.password))
        self.assertIsNone(args[3])
        self.assertEqual(args[4], 7)
        session = AppSession()
        self.assertTrue(session._establish(result, 0))
        self.assertEqual(session.userId, '12')
        self.assertTrue(session.hasPermission('APP_LOGIN'))

    def test_first_owner_optional_explicit_employee(self):
        result = self.setup_first_owner(42)
        self.assertEqual(result.employee_id, 42)
        self.assertEqual(self.repo.create_first_user.call_args.args[3], 42)

    def test_first_owner_accepts_six_character_password(self):
        self.password = 'abcdef'
        self.setup_first_owner()
        encoded = self.repo.create_first_user.call_args.args[2]
        self.assertTrue(self.passwords.verify(encoded, self.password))

    def test_first_owner_password_validation_before_any_writes(self):
        for password, confirmation in [('', ''), ('short', 'short'), (self.password, 'different-password')]:
            with self.subTest(length=len(password)):
                with self.assertRaises(ValueError):
                    self.service.create_first_owner('Owner', password, confirmation)
        self.repo.create_first_user.assert_not_called()
        self.assertEqual(self.commits, 0)

    def test_first_owner_concurrent_winner_is_rechecked_under_lock(self):
        self.repo.user_count.return_value = 0
        self.assertTrue(self.service.requires_first_run_setup())
        self.repo.has_users.return_value = True
        with self.assertRaises(SetupAlreadyCompleted):
            self.service.create_first_owner('Owner', self.password, self.password)
        names = [call[0] for call in self.repo.mock_calls]
        self.assertLess(names.index('acquire_bootstrap_lock'), names.index('has_users'))
        self.repo.create_first_user.assert_not_called()
        self.assertEqual(self.rollbacks, 1)

    def test_first_owner_failure_after_insert_rolls_back(self):
        self.repo.record_login.side_effect = RuntimeError('Simulated failure')
        with self.assertRaises(RuntimeError):
            self.setup_first_owner()
        self.assertEqual(self.commits, 0)
        self.assertEqual(self.rollbacks, 1)

    def test_first_owner_commit_failure_returns_no_verified_result(self):
        @contextmanager
        def fail_commit():
            yield object()
            raise RuntimeError('Commit failed')
        self.service._transaction = fail_commit
        with self.assertRaises(RuntimeError):
            self.setup_first_owner()

    def test_first_owner_normal_admin_login_and_no_blank_password(self):
        self.setup_first_owner()
        username, display_name, encoded, employee_id, role = self.repo.create_first_user.call_args.args
        self.user.update(id=12, password_hash=encoded, display_name=display_name, employee_id=employee_id)
        self.repo.find_credentials.return_value = dict(id=12, password_hash=encoded)
        restarted = AuthenticationService(self.transaction, lambda c: self.repo, self.passwords)
        self.assertEqual(restarted.authenticate('admin', self.password).user_id, 12)
        self.repo.find_credentials.assert_called_with('admin')
        with self.assertRaises(AuthenticationError):
            restarted.authenticate('admin', '')


class PermissionQueryTests(unittest.TestCase):
    def setUp(self):
        # Exercise production RBAC SELECT against an isolated in-memory fixture.
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE roles(id INTEGER, is_active INTEGER);
            CREATE TABLE permissions(id INTEGER, code TEXT, is_active INTEGER);
            CREATE TABLE user_roles(user_id INTEGER, role_id INTEGER);
            CREATE TABLE role_permissions(role_id INTEGER, permission_id INTEGER);
            INSERT INTO roles VALUES(1,1),(2,1),(3,0);
            INSERT INTO permissions VALUES(1,'APP_LOGIN',1),(2,'SETTINGS_MANAGE',1),(3,'DISABLED',0),(4,'HIDDEN',1);
            INSERT INTO user_roles VALUES(10,1),(10,2),(10,3);
            INSERT INTO role_permissions VALUES(1,1),(1,3),(2,1),(2,2),(3,4);
        ''')
        self.repo = AuthenticationRepository(None)
        def fetch(sql, params):
            cursor = self.db.execute(sql.replace('%s', '?').replace(' FOR UPDATE', ''), params)
            return [dict(zip([x[0] for x in cursor.description], row)) for row in cursor.fetchall()]
        self.repo._fetch_all = fetch

    def test_multiple_roles_union(self):
        self.assertEqual(self.repo.effective_permissions(10), {'APP_LOGIN', 'SETTINGS_MANAGE'})

    def test_inactive_role_cannot_grant(self):
        self.db.execute('UPDATE roles SET is_active=0 WHERE id=2')
        self.assertEqual(self.repo.effective_permissions(10), {'APP_LOGIN'})

    def test_inactive_permission_cannot_grant(self):
        self.db.execute('UPDATE permissions SET is_active=0 WHERE id=1')
        self.assertEqual(self.repo.effective_permissions(10), {'SETTINGS_MANAGE'})


if __name__ == '__main__':
    unittest.main()
