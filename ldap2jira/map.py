""" LDAP to JIRA User mapping

Package looks for given user names in LDAP.
Gets configurable fields for user name, email, name.
Tries to search those in JIRA and find a matching JIRA account.

Optionally consumes user map from file (json, csv).

Usage:
    * Configure instance
    * Use LDAP2JiraUserMap.find_jira_accounts method
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import json
import logging
import os

from jira import JIRA
from ldap2jira.ldap_lookup import LDAPLookup
from typing import List  # < python 3.9


log = logging.getLogger('ldap2jira.map')


NO_MATCH = 0
PARTIAL_MATCH = 1
MATCH = 2


class LDAP2JiraUserMap:
    """ Finds matching JIRA accounts for given user names

    Checks whether user name has LDAP record.
    Gets user names and email alternatives from LDAP.
    Looks for matching accounts in JIRA.

    Args:
        jira_url:
            JIRA server url ('https://issues.domain.org')
        jira_user:
            JIRA user to use for querying
        jira_password:
            JIRA user password to use for querying
        jira_auth_token:
            JIRA auth token to use for querying
        ldap_url:
            LDAP server host ('ldap://ldaphost')
        ldap_base:
            LDAP base for queries ('ou=users,dc=dep,dc=org')
        ldap_query_fields_username:
            Which LDAP fields to search given user name in
        ldap_fields_username:
            LDAP fields to match against JIRA account user name
        ldap_fields_mail:
            LDAP fields to match against JIRA account email
        ldap_fields_name:
            LDAP fields to match against JIRA account name
        ldap_fields_jira_search:
            Run JIRA search against those field values from LDAP
        email_domain:
            JIRA user email domain to match
        map_file:
            json or csv file with user mapping (username -> jira_username)

            csv example:

                us1csv,us1jira

                us2csv,us2jira

            json example:
                {

                    "us1json": "us1jira",

                    "us2json": "us2jira"

                }

    """

    def __init__(self,
                 jira_url: str,
                 ldap_url: str,
                 ldap_base: str,
                 ldap_query_fields_username: List[str],
                 ldap_fields_username: List[str],
                 ldap_fields_mail: List[str],
                 ldap_fields_name: List[str],
                 ldap_fields_jira_search: List[str],
                 email_domain: str,
                 map_file: str = None,
                 jira_user: str = None,
                 jira_password: str = None,
                 jira_auth_token: str = None,
                 jira_extra_kwargs=None
                 ):

        if jira_user or jira_password:
            if not all((jira_user, jira_password)):
                raise ValueError('JIRA user and password required for basic auth.')
        elif not jira_auth_token:
            raise ValueError('JIRA user/password or auth token required.')

        self.jira_url = jira_url
        self.jira_user = jira_user
        self.jira_password = jira_password
        self.jira_auth_token = jira_auth_token
        self.jira_extra_kwargs = jira_extra_kwargs or {}

        self.ldap_url = ldap_url
        self.ldap_base = ldap_base

        self.ldap_query_fields_username = ldap_query_fields_username

        self.ldap_fields_username = ldap_fields_username
        self.ldap_fields_mail = ldap_fields_mail
        self.ldap_fields_name = ldap_fields_name
        self.ldap_fields_jira_search = ldap_fields_jira_search

        self.email_domain = email_domain.lstrip('@')

        self._ldap = None
        self._jira = None

        self.map_file = map_file
        self.map = {}

    @property
    def ldap(self) -> LDAPLookup:
        if not self._ldap:
            self._ldap = LDAPLookup(self.ldap_url, self.ldap_base)

        return self._ldap

    @property
    def jira(self) -> JIRA:
        if not self._jira:  # pragma: no cover
            jira_kwargs = {
                'options': {'server': self.jira_url},
                'get_server_info': False,
            }
            if self.jira_password:
                jira_kwargs['basic_auth'] = (self.jira_user, self.jira_password)
            else:
                jira_kwargs['token_auth'] = self.jira_auth_token

            self._jira = JIRA(**jira_kwargs, **self.jira_extra_kwargs)

        return self._jira

    def load_map(self, filename: str = None):
        if not filename:
            return {}

        if not os.path.exists(filename):
            log.warning("Map file doesn't exist: %s", filename)
            return {}

        file_extension = os.path.splitext(filename)[1]
        fmap = {}

        with open(filename, 'r') as map_fp:
            if file_extension == '.json':
                fmap = json.load(map_fp)

            if file_extension == '.csv':
                fmap = {val_list[0]: val_list[1]
                        for val_list in csv.reader(map_fp)}

        return fmap

    def ldap_query(self, query: str):
        return_fields = set(
            self.ldap_fields_username
            + self.ldap_fields_mail
            + self.ldap_fields_jira_search
        )

        return self.ldap.query(
            query,
            query_fields=self.ldap_query_fields_username,
            return_fields=return_fields
        )

    def jira_search_user(self, query: str):
        log.info('Jira search for: %s', query)
        return self.jira.search_users(query, maxResults=10)

    def _ldap_jira_match(self,
                         ldap_account: dict,
                         jira_account: object
                         ) -> int:
        """ Compare LDAP result with JIRA account

        Args:
            ldap_account: LDAP result
            jira_account: JIRA account to compare LDAP with

        Returns:
            Either MATCH, PARTIAL_MATCH, NO_MATCH
        """
        try:
            jira_username = jira_account.name
            jira_email = jira_account.emailAddress
            jira_names = {jira_account.name, jira_account.displayName}

        except AttributeError as e:
            log.warning('Unable to get JIRA account values\n%s', e)
            return NO_MATCH

        log.debug('Trying JIRA account: %s [%s] %s',
                  jira_account.displayName, jira_username, jira_email)

        if jira_email.endswith(f'@{self.email_domain}'):

            ldap_emails = {ldap_account[f]
                           for f in self.ldap_fields_mail
                           if f in ldap_account}

            ldap_usernames = {ldap_account[f]
                              for f in self.ldap_fields_username
                              if f in ldap_account}

            email_match = jira_email in ldap_emails
            username_match = jira_username in ldap_usernames

            if email_match or username_match:
                log.debug('Match')
                return MATCH

            ldap_names = {ldap_account[f]
                          for f in self.ldap_fields_name
                          if f in ldap_account}

            if jira_names & ldap_names:
                log.debug('Partial Match')
                return PARTIAL_MATCH

        log.debug('No Match')
        return NO_MATCH

    def _update_user(self,
                     user_dict: dict,
                     username: str,
                     status: str,
                     log_extra: str = '',
                     level=logging.WARNING
                     ):
        """ Update common user dict and log result """
        user_dict['status'] = status

        log_msg = (
            "JIRA account - "
            f"{status.replace('_', ' ').capitalize()}: {username}\n")
        log_msg += log_extra + '\n' if log_extra else ''
        log.log(level, log_msg)

    def _process_username(self, username: str) -> dict:
        """ Process single username

        Multithreaded worker method
        """
        user_dict = {'username': username}

        if not username:
            return user_dict

        log.info('Process username: %s', username)

        # Try file map
        if username in self.map:
            self._update_user(user_dict,
                              self.map[username],
                              'found',
                              log_extra='File Map',
                              level=logging.INFO)
            user_dict['jira-account'] = self.map[username]
            return user_dict

        # No luck - continue
        ldap_results = self.ldap_query(username)

        if not ldap_results:
            self._update_user(user_dict, username, 'not_in_ldap')
            return user_dict

        elif len(ldap_results) > 1:
            # Shouldn't happen when searching unique ldap field for match
            self._update_user(user_dict, username, 'missing')
            log.error('Multiple LDAP records for uid %s', username)
            return user_dict

        ldap_account = ldap_results[0]

        # All the values to search JIRA for in order
        jira_queries = []
        for field in self.ldap_fields_jira_search:
            if (
                field not in ldap_account
                or not ldap_account[field]
            ):
                log.debug('Field %s not in LDAP results', field)
                continue

            if ldap_account[field] not in jira_queries:
                jira_queries.append(ldap_account[field])

        # Look for jira account based on various ldap fields by preference
        jira_account_usernames = set()
        partial_single_matches = []  # Need order of preference

        for query in jira_queries:

            result_jira_accounts = self.jira_search_user(query)
            single_result = len(result_jira_accounts) == 1
            for jira_account in result_jira_accounts:
                if jira_account.name in jira_account_usernames and not single_result:
                    continue

                jira_account_usernames.add(jira_account.name)

                match = self._ldap_jira_match(ldap_account, jira_account)
                if match == MATCH:
                    self._update_user(user_dict,
                                      jira_account.name,
                                      'found',
                                      level=logging.INFO)
                    user_dict['jira-account'] = jira_account.name
                    break

                if match == PARTIAL_MATCH and single_result:
                    if jira_account.name not in partial_single_matches:
                        partial_single_matches.append(jira_account.name)

            # Don't search value from rest of ldap fields
            if 'jira-account' in user_dict:
                break

        if not jira_account_usernames:
            self._update_user(user_dict, username, 'missing')
            return user_dict

        if len(partial_single_matches) == 1:
            self._update_user(user_dict,
                              partial_single_matches[0],
                              'found',
                              log_extra='Single Partial',
                              level=logging.INFO)
            user_dict['jira-account'] = partial_single_matches[0]

        if 'jira-account' not in user_dict:
            user_dict['jira-results'] = jira_account_usernames

            self._update_user(
                user_dict, username, 'ambiguous',
                'Possible matches: ' + ', '.join(user_dict['jira-results']))

        return user_dict

    def find_jira_accounts(self, usernames: List[str], max_workers: int = None) -> dict:
        """ Finds matching JIRA account for given user names

        Args:
            usernames: List of user names
            max_workers: The maximum number of threads

        Returns:
            A dict with user names as keys and match results dict as values

            Possible match result keys:
                status:
                    found: Found good match in JIRA

                    missing: No match found in JIRA

                    ambiguous: No good match, possible matches in jira-results

                    not_in_ldap: User name wasn't found in LDAP

                jira-account: JIRA user name

                jira-results: A list of JIRA user names that partially match

            Example:

            {
                'us1': {'jira-results': ['us1'], 'status': 'ambiguous'},

                'us2': {'status': 'missing'},

                'us3': {'status': 'not_in_ldap'},

                'us4': {'jira-results': ['us1', 'us3'],
                        'status': 'ambiguous'},

                'us5': {'jira-account': 'us5', 'status': 'found'}

            }
        """
        users = {}

        self.map.update(self.load_map(self.map_file))

        with ThreadPoolExecutor(thread_name_prefix='W', max_workers=max_workers) as executor:

            f_users_d = {executor.submit(self._process_username, username)
                         for username in usernames}

            for f_user_d in as_completed(f_users_d):
                user_d = f_user_d.result()

                username = user_d.pop('username')

                if username:
                    users[username] = user_d

        return users
