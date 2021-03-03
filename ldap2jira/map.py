from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
# TODO: Waiting for py39
from typing import List

from jira import JIRA
from ldap2jira.ldap_lookup import LDAPLookup


log = logging.getLogger('ldap2jira.map')


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
        ldap_url:
            LDAP server host ('ldap://ldaphost')
        ldap_base:
            LDAP base for queries ('ou=users,dc=dep,dc=org')
        ldap_query_fields_username:
            Which LDAP fields to search given user name in
        ldap_fields_username:
            LDAP fields to match against JIRA user name
        ldap_fields_mail:
            LDAP fields to match against JIRA user email
        ldap_fields_jira_search:
            Run JIRA search against those field values from LDAP
        email_domain:
            JIRA user email domain to match
    """

    def __init__(self,
                 jira_url: str,
                 jira_user: str,
                 jira_password: str,
                 ldap_url: str,
                 ldap_base: str,
                 ldap_query_fields_username: List[str],
                 ldap_fields_username: List[str],
                 ldap_fields_mail: List[str],
                 ldap_fields_jira_search: List[str],
                 email_domain: str,
                 ):

        self.jira_url = jira_url
        self.jira_user = jira_user
        self.jira_password = jira_password

        self.ldap_url = ldap_url
        self.ldap_base = ldap_base

        self.ldap_query_fields_username = ldap_query_fields_username

        self.ldap_fields_username = ldap_fields_username
        self.ldap_fields_mail = ldap_fields_mail
        self.ldap_fields_jira_search = ldap_fields_jira_search

        self.email_domain = email_domain.lstrip('@')

        self._ldap = None
        self._jira = None

    @property
    def ldap(self) -> LDAPLookup:
        if not self._ldap:
            self._ldap = LDAPLookup(self.ldap_url, self.ldap_base)

        return self._ldap

    @property
    def jira(self) -> JIRA:
        if not self._jira:  # pragma: no cover
            self._jira = JIRA(basic_auth=(self.jira_user, self.jira_password),
                              options=dict(server=self.jira_url),
                              get_server_info=False)
        return self._jira

    def ldap_query(self, query: str):
        return self.ldap.query(
            query,
            query_fields=self.ldap_query_fields_username,
            return_fields=self.ldap_fields_username + self.ldap_fields_mail
        )

    def jira_search_user(self, query: str):
        log.info('Jira search for: %s', query)
        return self.jira.search_users(query, maxResults=10)

    def ldap_jira_match(self,
                        ldap_account: dict,
                        jira_account: object
                        ) -> bool:

        jira_username = jira_account.key
        jira_email = jira_account.emailAddress

        if not jira_email.endswith(f'@{self.email_domain}'):
            return False

        ldap_emails = {ldap_account[f]
                       for f in self.ldap_fields_mail
                       if f in ldap_account}

        ldap_usernames = {ldap_account[f]
                          for f in self.ldap_fields_username
                          if f in ldap_account}

        email_match = jira_email in ldap_emails
        username_match = jira_username in ldap_usernames

        return email_match or username_match

    def process_username(self, username: str) -> dict:

        def update_and_log_user(username: str,
                                status: str,
                                log_extra: str = '',
                                level=logging.WARNING
                                ):

            user_dict['status'] = status

            log_msg = (
                "JIRA account - "
                f"{status.replace('_', ' ').capitalize()}: {username}\n")
            log_msg += log_extra + '\n' if log_extra else ''
            log.log(level, log_msg)

        user_dict = {'username': username}

        if not username:
            return user_dict

        log.info('Process username: %s', username)

        ldap_results = self.ldap_query(username)

        if not ldap_results:
            update_and_log_user(username, 'not_in_ldap')
            return user_dict

        elif len(ldap_results) > 1:
            # Shouldn't happen when searching unique ldap field for match
            update_and_log_user(username, 'missing')
            log.error('Multiple LDAP records for uid %s', username)
            return user_dict

        ldap_account = ldap_results[0]

        # Look for jira account based on various ldap fields by preference
        jira_accounts = []

        for field in self.ldap_fields_jira_search:
            if (
                field not in ldap_account
                or not ldap_account[field]
            ):
                log.debug('Field %s not in LDAP results', field)
                continue

            for jira_account in self.jira_search_user(ldap_account[field]):
                if jira_account in jira_accounts:
                    continue

                jira_accounts.append(jira_account)

                if self.ldap_jira_match(ldap_account, jira_account):
                    update_and_log_user(username, 'found', level=logging.INFO)
                    user_dict['jira-account'] = jira_account.key
                    break

            # Don't search value from rest of ldap fields
            if 'jira-account' in user_dict:
                break

        if not jira_accounts:
            update_and_log_user(username, 'missing')
            return user_dict

        if 'jira-account' not in user_dict:
            user_dict['jira-results'] = [
                jira_account.key for jira_account in jira_accounts]

            update_and_log_user(
                username, 'ambiguous',
                'Possible matches: ' + ','.join(user_dict['jira-results']))

        return user_dict

    def find_jira_accounts(self, usernames: List[str]) -> dict:
        """ Finds matching JIRA account for given user names

        Args:
            usernames: List of user names

        Returns:
            A dict with user names as keys and match results dict as values

            Possible match result keys:
                status:
                    found: Found good match in JIRA

                    missing: No match found in JIRA

                    ambiguous: No good match, possible matches in jira-results

                    not_in_ldap: User name wasn't found in LDAP

                jira-account: JIRA user key

                jira-results: A list of JIRA user keys that partially match

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

        with ThreadPoolExecutor(thread_name_prefix='W') as executor:

            f_users_d = {executor.submit(self.process_username, username)
                         for username in usernames}

            for f_user_d in as_completed(f_users_d):
                user_d = f_user_d.result()

                username = user_d.pop('username')

                if username:
                    users[username] = user_d

        return users

    def load_map_from_file(self, filename: str):
        # TODO: Ability to load map for certain ldap users from csv file
        pass  # pragma: no cover
