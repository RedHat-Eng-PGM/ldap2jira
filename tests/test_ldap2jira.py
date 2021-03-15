from collections import namedtuple
from copy import deepcopy
import os
import unittest
from unittest.mock import patch, ANY, MagicMock

from ldap2jira.ldap_lookup import LDAPLookup, LDAPQueryNotFoundError
from ldap2jira.map import LDAP2JiraUserMap


class LdapMockTestCaseBase(unittest.TestCase):

    ldap_url = 'ldap://localhost'
    ldap_base = 'ou=users'

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
        cls.ldap = LDAPLookup(cls.ldap_url, cls.ldap_base)


@patch('ldap.ldapobject.LDAPObject.search_s')
class LDAPTestCase(LdapMockTestCaseBase):

    def assert_mock_called(self, mock, query, return_fields=ANY):
        return mock.assert_called_once_with(
            self.ldap_base, ANY, query, return_fields)

    def test_ldap_no_result(self, mock):
        query = 'nonexistent'
        mock.return_value = []

        self.assertEqual(self.ldap.query(query), [])
        self.assert_mock_called(mock, f'uid={query}')

    def test_ldap_no_result_exception(self, mock):
        query = 'nonexistent'
        mock.return_value = []

        with self.assertRaises(LDAPQueryNotFoundError):
            self.ldap.query(query, raise_exception=True)

        self.assert_mock_called(mock, f'uid={query}')

    def test_ldap_single_result(self, mock):
        query = 'us1'
        mock.return_value = [self.ldap_mock_results[0]]

        self.assertEqual(
            self.ldap.query(query),
            [{'uid': 'us1', 'cn': 'user 1', 'mail': 'us1@org.com'}]
        )
        self.assert_mock_called(mock, f'uid={query}')

    def test_ldap_multiple_results(self, mock):
        query = 'us'
        mock.return_value = self.ldap_mock_results

        res = self.ldap.query(query, query_fields=['uid', 'cn'])
        self.assertEqual(res, [
            {'uid': 'us1', 'cn': 'user 1', 'mail': 'us1@org.com'},
            {'uid': 'us2', 'cn': 'user 2', 'mail': 'us2@org.com'}
        ])
        self.assert_mock_called(mock, f'(|(uid={query}*)(cn={query}*))')

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


@patch('ldap.ldapobject.LDAPObject.search_s')
class LDAP2JiraTestCase(LdapMockTestCaseBase):
    jira_account_mock = namedtuple(
        'JiraAccount', ['key', 'emailAddress', 'displayName', 'name'])

    jira_accounts_mock = [
        jira_account_mock('us1', 'us1@nottest.org', 'U S1', 'U S1'),
        jira_account_mock('us2', 'us2@test.org', 'U S2', 'U S2'),
        jira_account_mock('us3', 'us3@test.org', 'U S3', 'U S3'),
    ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.map = LDAP2JiraUserMap(
            jira_url='https://test.org',
            jira_user='test',
            jira_password='test',
            ldap_url=cls.ldap_url,
            ldap_base=cls.ldap_base,
            ldap_query_fields_username=['uid'],
            ldap_fields_username=['uid'],
            ldap_fields_mail=['mail'],
            ldap_fields_name=['cn'],
            ldap_fields_jira_search=[
                'mail', 'uid', 'cn'],
            email_domain='test.org'
            )

        cls.map._jira = MagicMock()

    def setUp(self):
        super().setUp()
        self.map._jira.search_users = MagicMock()
        self.mock_jira_search = self.map._jira.search_users

    def test_jira_match(self, mock_ldap):
        mock_ldap.return_value = [self.ldap_mock_results[1]]
        self.mock_jira_search.return_value = [self.jira_accounts_mock[1]]

        self.assertDictEqual(
            self.map.find_jira_accounts(['us2']),
            {'us2': {'jira-account': 'us2', 'status': 'found'}}
        )

    def test_jira_ambiguous(self, mock_ldap):
        mock_ldap.return_value = [self.ldap_mock_results[1]]
        self.mock_jira_search.return_value = [self.jira_accounts_mock[0],
                                              self.jira_accounts_mock[2]]

        with self.assertLogs('ldap2jira.map', level='WARNING'):

            self.assertDictEqual(
                self.map.find_jira_accounts(['us2']),
                {'us2': {'jira-results': {'us1', 'us3'},
                         'status': 'ambiguous'}}
            )

    def test_jira_not_in_ldap(self, mock_ldap):
        mock_ldap.return_value = []

        with self.assertLogs('ldap2jira.map', level='WARNING'):

            self.assertDictEqual(
                self.map.find_jira_accounts(['us2']),
                {'us2': {'status': 'not_in_ldap'}}
            )

    def test_jira_missing(self, mock_ldap):
        mock_ldap.return_value = [self.ldap_mock_results[1]]

        with self.assertLogs('ldap2jira.map', level='WARNING'):

            self.assertDictEqual(
                self.map.find_jira_accounts(['us2']),
                {'us2': {'status': 'missing'}}
            )

    def test_jira_epmty_username(self, mock_ldap):
        self.assertDictEqual(self.map.find_jira_accounts(['']), {})

    def test_jira_multiple_ldap(self, mock_ldap):
        mock_ldap.return_value = self.ldap_mock_results

        with self.assertLogs('ldap2jira.map', level='WARNING'):

            self.assertDictEqual(
                self.map.find_jira_accounts(['us2']),
                {'us2': {'status': 'missing'}}
            )

    def test_skip_invalid_ldap_field(self, mock_ldap):
        mock_ldap.return_value = [self.ldap_mock_results[1]]

        self.map.ldap_fields_jira_search = ['wrongfield', 'mail', 'uid']

        with self.assertLogs('ldap2jira.map', level='WARNING'):

            self.assertDictEqual(
                self.map.find_jira_accounts(['us2']),
                {'us2': {'status': 'missing'}}
            )

        # Empty LDAP value should also be ignored
        empty_field_mock = deepcopy(self.ldap_mock_results[1])
        empty_field_mock[1]['mail'] = [b'']
        mock_ldap.return_value = [empty_field_mock]

        self.map.ldap_fields_jira_search = ['mail', 'uid']

        with self.assertLogs('ldap2jira.map', level='WARNING'):

            self.assertDictEqual(
                self.map.find_jira_accounts(['us2']),
                {'us2': {'status': 'missing'}}
            )

    def test_skip_same_ldap_field_value(self, mock_ldap):
        local_mock = deepcopy(self.ldap_mock_results[1])
        local_mock[1]['mail'] = local_mock[1]['uid']
        mock_ldap.return_value = [local_mock]

        self.map.ldap_fields_jira_search = ['mail', 'uid']

        with self.assertLogs('ldap2jira.map', level='WARNING'):

            self.assertDictEqual(
                self.map.find_jira_accounts(['us2']),
                {'us2': {'status': 'missing'}}
            )

    def test_partial_match(self, mock_ldap):
        self.mock_jira_search.return_value = [self.jira_accounts_mock[1]]

        local_mock = deepcopy(self.ldap_mock_results[1])
        local_mock[1]['uid'] = [b'nomatch']
        local_mock[1]['mail'] = [
            f'nomatch@{self.map.email_domain}'.encode('utf-8')]
        local_mock[1]['cn'] = [b'U S2']
        mock_ldap.return_value = [local_mock]

        self.assertDictEqual(
            self.map.find_jira_accounts(['us2']),
            {'us2':  {'jira-account': 'us2', 'status': 'found'}}
        )

    def test_wrong_email(self, mock_ldap):
        mock_ldap.return_value = [self.ldap_mock_results[0]]
        self.mock_jira_search.return_value = [self.jira_accounts_mock[0]]

        with self.assertLogs('ldap2jira.map', level='WARNING'):

            self.assertDictEqual(
                self.map.find_jira_accounts(['us1']),
                {'us1': {'jira-results': {'us1'}, 'status': 'ambiguous'}}
            )

    def test_file_map(self, mock_ldap):
        # Missing file
        self.map.map_file = os.path.join(os.path.dirname(__file__),
                                         'notexisting')

        with self.assertLogs('ldap2jira.map', level='WARNING'):

            self.assertDictEqual(
                self.map.find_jira_accounts(['us1']),
                {'us1': {'status': 'not_in_ldap'}}
            )

        # JSON
        self.map.map_file = os.path.join(os.path.dirname(__file__),
                                         'test_map.json')

        self.assertDictEqual(
            self.map.find_jira_accounts(['us1json']),
            {'us1json': {'jira-account': 'us1jira', 'status': 'found'}}
        )

        # CSV
        self.map.map_file = os.path.join(os.path.dirname(__file__),
                                         'test_map.csv')

        self.assertDictEqual(
            self.map.find_jira_accounts(['us1csv']),
            {'us1csv': {'jira-account': 'us1jira', 'status': 'found'}}
        )

        # Reset shared mapper instance
        self.map.map_file = None
        self.map = {}
