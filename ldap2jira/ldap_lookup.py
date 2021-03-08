import ldap
from typing import List  # < python 3.9


class LDAPQueryNotFoundError(Exception):
    pass


class LDAPLookup:
    """ Wraps ldap library query

    Args:
        ldap_url: LDAP server in the form of 'ldap://ldaphost'
        ldap_base: LDAP base for search ('ou=users,dc=department,dc=org')
    """

    DEFAULT_QUERY_FIELDS: List[str] = ['uid']
    DEFAULT_RETURN_FIELDS: List[str] = ['uid', 'cn', 'mail']

    def __init__(self, ldap_url: str, ldap_base: str):
        self.ldap_url = ldap_url
        self.ldap_base = ldap_base

        self._ldap_client = None  # lazy client init

    @property
    def ldap_client(self):
        if not self._ldap_client:
            self._ldap_client = ldap.initialize(self.ldap_url)

            self._ldap_client.set_option(ldap.OPT_PROTOCOL_VERSION, 3)
            self._ldap_client.set_option(ldap.OPT_REFERRALS, 0)

        return self._ldap_client

    def query(self, query: str,
              query_fields: List[str] = None,
              return_fields: List[str] = None,
              raise_exception: bool = False,
              ) -> List[dict]:
        """ Perform LDAP query

        Args:
            query: String to search
            query_fields: Which LDAP fields to search in
            return_fields: What LDAP fields to return
            raise_exception: If True - raise exception if no results

        Returns:
            List if dicts with LDAP results

            Example:

            [
                {'uid': 'us1', 'cn': 'user 1', 'mail': 'us1@org.com'},

                {'uid': 'us2', 'cn': 'user 2', 'mail': 'us2@org.com'}

            ]

        Raises:
            LDAPQueryNotFoundError: No result while raise_exception True
        """

        query = query.rstrip('*')

        if not query_fields:
            query_fields = self.DEFAULT_QUERY_FIELDS

        if not return_fields:
            return_fields = self.DEFAULT_RETURN_FIELDS

        if len(query_fields) == 1:
            query_string = f'{query_fields[0]}={query}'
        else:
            # Example: (|(cn=query*)(sn=query*)(mail=query*))
            field_queries = [f'({field}={query}*)'
                             for field in query_fields if field]
            query_string = '(|%s)' % ''.join(field_queries)

        res = self.ldap_client.search_s(
            self.ldap_base, ldap.SCOPE_SUBTREE, query_string, return_fields)

        if raise_exception and not res:
            raise LDAPQueryNotFoundError(f'Query not found in LDAP: {query}')

        # Extract first values, convert from bytes
        return [{k: v[0].decode('utf-8')
                 for k, v in record[1].items()} for record in res]
