from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

from jira import JIRA

from ldap2jira.ldap_lookup import LDAPLookup


log = logging.getLogger('ldap2jira.map')


class LDAP2JiraUserMap:
    def __init__(self,
                 jira_url: str,
                 jira_user: str,
                 jira_password: str,
                 ldap_url: str,
                 ldap_base: str,
                 ldap_query_fields_username: list[str],
                 ldap_fields_username: list[str],
                 ldap_fields_mail: list[str],
                 ldap_fields_jira_search: list[str],
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
        if not self._jira:
            self._jira = JIRA(basic_auth=(self.jira_user, self.jira_password),
                              options=dict(server=self.jira_url))
        return self._jira

    def ldap_query(self, query: str):
        return self.ldap.query(query,
                               query_fields=self.ldap_query_fields_username,
                               return_fields=self.ldap_fields_username + self.ldap_fields_mail
                               )

    def jira_search_user(self, query: str):
        log.info(f'Jira search for: {query}')
        return self.jira.search_users(query, maxResults=10)

    def ldap_jira_match(self, ldap_account: dict, jira_account) -> bool:
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

        def update_and_log_user(
                username: str, status: str,  log_extra: str = '', level=logging.WARNING):

            user_dict['status'] = status

            log_msg = f"JIRA account - {status.replace('_', ' ').capitalize()}: {username}\n"
            log_msg += log_extra + '\n' if log_extra else ''
            log.log(level, log_msg)

        user_dict = {'username': username}

        if not username:
            return user_dict

        log.info(f'Process username: {username}')

        ldap_results = self.ldap_query(username)

        if not ldap_results:
            update_and_log_user(username, 'not_in_ldap')
            return user_dict

        elif len(ldap_results) > 1:
            # Shouldn't happen when searching unique ldap field for match
            update_and_log_user(username, 'missing')
            log.error(f'Multiple LDAP records for uid {username}')
            return user_dict

        ldap_account = ldap_results[0]

        # Try to look for jira account based on various ldap fields by preference
        jira_accounts = []
        searched_values = set()

        for field in self.ldap_fields_jira_search:
            if (field not in ldap_account
                    or not ldap_account[field]
                    or ldap_account[field] in searched_values):
                continue

            searched_values.add(ldap_account[field])

            for jira_account in self.jira_search_user(ldap_account[field]):
                if jira_account in jira_accounts:
                    continue

                jira_accounts.append(jira_account)

                if self.ldap_jira_match(ldap_account, jira_account):
                    update_and_log_user(username, 'found', level=logging.INFO)
                    user_dict['jira-account'] = jira_account.key
                    break

            if 'jira-account' in user_dict:  # Don't search value from rest of ldap fields
                break

        if not jira_accounts:
            update_and_log_user(username, 'missing')
            return user_dict

        if 'jira-account' not in user_dict:
            user_dict['jira-results'] = [
                jira_account.key for jira_account in jira_accounts]

            update_and_log_user(username, 'ambiguous',
                                'Possible matches: ' + ','.join(user_dict['jira-results']))

        return user_dict

    def find_jira_accounts(self, usernames: list[str]) -> dict:
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
        pass
