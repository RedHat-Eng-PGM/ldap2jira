from copy import deepcopy
import unittest
from unittest.mock import patch, ANY

from ldap2jira.ldap_lookup import LDAPLookup, LDAPQueryNotFoundError


class LDAPTestCase(unittest.TestCase):

    ldap_mock_results = [
        (
            'uid=us1,ou=users,dc=org,dc=com',
            {'uid': [b'us1'], 'cn': [b'user 1'], 'mail': [b'us1@org.com']}
        ),
        (
            'uid=us2,ou=users,dc=org,dc=com',
            {'uid': [b'us2'], 'cn': [b'user 2'], 'mail': [b'us2@org.com']}
        ),
    ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ldap_url = 'ldap://localhost'
        cls.ldap_base = 'ou=users'
        cls.ldap = LDAPLookup(cls.ldap_url, cls.ldap_base)

    def setUp(self):
        super().setUp()

    def assert_mock_called(self, mock, query, return_fields=ANY):
        return mock.assert_called_once_with(
            self.ldap_base, ANY, query, return_fields)

    @patch('ldap.ldapobject.LDAPObject.search_s', return_value=[])
    def test_ldap_no_result(self, mock):
        query = 'nonexistent'

        self.assertEqual(self.ldap.query(query), [])
        self.assert_mock_called(mock, f'uid={query}')

    @patch('ldap.ldapobject.LDAPObject.search_s', return_value=[])
    def test_ldap_no_result_exception(self, mock):
        query = 'nonexistent'

        with self.assertRaises(LDAPQueryNotFoundError):
            self.ldap.query(query, raise_exception=True)

        self.assert_mock_called(mock, f'uid={query}')

    @patch('ldap.ldapobject.LDAPObject.search_s')
    def test_ldap_single_result(self, mock):
        query = 'us1'
        mock.return_value = [self.ldap_mock_results[0]]

        self.assertEqual(
            self.ldap.query(query),
            [{'uid': 'us1', 'cn': 'user 1', 'mail': 'us1@org.com'}]
        )
        self.assert_mock_called(mock, f'uid={query}')

    @patch('ldap.ldapobject.LDAPObject.search_s')
    def test_ldap_multiple_results(self, mock):
        query = 'us'
        mock.return_value = self.ldap_mock_results

        res = self.ldap.query(query, query_fields=['uid', 'cn'])
        self.assertEqual(res, [
            {'uid': 'us1', 'cn': 'user 1', 'mail': 'us1@org.com'},
            {'uid': 'us2', 'cn': 'user 2', 'mail': 'us2@org.com'}
        ])
        self.assert_mock_called(mock, f'(|(uid={query}*)(cn={query}*))')

    @patch('ldap.ldapobject.LDAPObject.search_s')
    def test_ldap_return_fields(self, mock):
        query = 'us'
        return_fields = ['uid', 'cn']

        mock_return_value = deepcopy(self.ldap_mock_results)
        for m in mock_return_value:
            del m[1]['mail']
        mock.return_value = mock_return_value

        res = self.ldap.query(query, return_fields=return_fields)
        self.assertEqual(res, [
            {'uid': 'us1', 'cn': 'user 1'},
            {'uid': 'us2', 'cn': 'user 2'}
        ])
        self.assert_mock_called(
            mock, f'uid={query}', return_fields)
