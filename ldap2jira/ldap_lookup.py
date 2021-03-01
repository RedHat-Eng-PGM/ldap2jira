import ldap


class LDAPQueryNotFoundError(Exception):
    pass


class LDAPLookup:
    DEFAULT_QUERY_FIELDS: list[str] = ['uid']
    DEFAULT_RETURN_FIELDS: list[str] = ['uid', 'cn', 'mail']

    def __init__(self, ldap_url: str, ldap_base: str):
        self.ldap_client = ldap.initialize(ldap_url)
        self.ldap_base = ldap_base

        self.ldap_client.set_option(ldap.OPT_PROTOCOL_VERSION, 3)
        self.ldap_client.set_option(ldap.OPT_REFERRALS, 0)

    def query(self, query: str,
              query_fields: list[str] = None,
              return_fields: list[str] = None,
              raise_exception: bool = False,
              ) -> list[dict]:

        query = query.rstrip('*')

        if not query_fields:
            query_fields = self.DEFAULT_QUERY_FIELDS

        if not return_fields:
            return_fields = self.DEFAULT_RETURN_FIELDS

        if len(query_fields) == 1:
            query_string = f'{query_fields[0]}={query}'
        else:
            # Example: (|(cn=query*)(sn=query*)(mail=query*))
            field_queries = [f'({field}={query}*)' for field in query_fields if field]
            query_string = '(|%s)' % ''.join(field_queries)

        res = self.ldap_client.search_s(
            self.ldap_base, ldap.SCOPE_SUBTREE, query_string, return_fields)

        if raise_exception and not res:
            raise LDAPQueryNotFoundError(f'Query not found in LDAP: {query}')

        # Extract first values, convert from bytes
        return [{k: v[0].decode('utf-8') for k, v in record[1].items()} for record in res]
