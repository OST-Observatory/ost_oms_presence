"""Unit tests for the schema-agnostic parts of ldap_auth (no server needed)."""

import ldap_auth

GROUP = 'cn=observatory,ou=groups,dc=test,dc=invalid'
USER_DN = 'uid=alice,ou=people,dc=test,dc=invalid'


def test_parse_group_dns_splits_on_semicolon():
    value = 'cn=a,dc=x; cn=b,dc=x\ncn=c,dc=x'
    assert ldap_auth.parse_group_dns(value) == ['cn=a,dc=x', 'cn=b,dc=x', 'cn=c,dc=x']


def test_parse_group_dns_empty():
    assert ldap_auth.parse_group_dns('') == []
    assert ldap_auth.parse_group_dns(None) == []


def test_member_of_schema():
    assert ldap_auth.matches_group(GROUP, USER_DN, 'alice', member_of=[GROUP])


def test_member_of_is_case_insensitive():
    assert ldap_auth.matches_group(GROUP, USER_DN, 'alice', member_of=[GROUP.upper()])


def test_group_of_names_schema():
    attrs = {'member': [USER_DN, 'uid=bob,ou=people,dc=test,dc=invalid']}
    assert ldap_auth.matches_group(GROUP, USER_DN, 'alice', group_attrs=attrs)


def test_posix_group_schema():
    attrs = {'memberUid': ['bob', 'alice']}
    assert ldap_auth.matches_group(GROUP, USER_DN, 'alice', group_attrs=attrs)


def test_posix_group_accepts_bytes():
    attrs = {'memberUid': [b'alice']}
    assert ldap_auth.matches_group(GROUP, USER_DN, 'alice', group_attrs=attrs)


def test_non_member_rejected():
    attrs = {'member': ['uid=bob,ou=people,dc=test,dc=invalid'], 'memberUid': ['bob']}
    assert not ldap_auth.matches_group(GROUP, USER_DN, 'alice', group_attrs=attrs)
    assert not ldap_auth.matches_group(GROUP, USER_DN, 'alice', member_of=['cn=other,dc=x'])


def test_uid_is_not_matched_against_member_dns():
    attrs = {'member': ['alice']}
    assert not ldap_auth.matches_group(GROUP, USER_DN, 'alice', group_attrs=attrs)


class FakeConnection:
    """Minimal stand-in for an ldap3 Connection returning one group entry."""

    def __init__(self, entries):
        self.entries = entries
        self.response = []
        self.searched = []

    def search(self, search_base, search_filter, search_scope=None, attributes=None):
        self.searched.append(search_base)
        attrs = self.entries.get(search_base)
        if attrs is None:
            self.response = []
            return False
        self.response = [{'dn': search_base, 'attributes': attrs}]
        return True


def test_user_in_any_group_reads_group_when_member_of_is_absent():
    conn = FakeConnection({GROUP: {'memberUid': ['alice']}})
    matched = ldap_auth.user_in_any_group(conn, USER_DN, 'alice', [], [GROUP])
    assert matched == [GROUP]
    assert conn.searched == [GROUP]


def test_user_in_any_group_skips_lookup_when_member_of_matches():
    conn = FakeConnection({})
    matched = ldap_auth.user_in_any_group(conn, USER_DN, 'alice', [GROUP], [GROUP])
    assert matched == [GROUP]
    assert conn.searched == []


def test_user_in_any_group_returns_every_match():
    other = 'cn=staff,ou=groups,dc=test,dc=invalid'
    conn = FakeConnection({other: {'memberUid': ['alice']}})
    matched = ldap_auth.user_in_any_group(conn, USER_DN, 'alice', [GROUP], [GROUP, other])
    assert matched == [GROUP, other]


def test_user_in_any_group_empty_when_no_match():
    conn = FakeConnection({GROUP: {'memberUid': ['bob']}})
    assert ldap_auth.user_in_any_group(conn, USER_DN, 'alice', [], [GROUP]) == []


def test_unreadable_group_is_not_a_match():
    conn = FakeConnection({})
    assert ldap_auth.user_in_any_group(conn, USER_DN, 'alice', [], [GROUP]) == []


def test_tls_problem_flags_plain_ldap():
    config = ldap_auth.LdapConfig(server_uri='ldap://dir.test.invalid', start_tls=False)
    assert ldap_auth.tls_problem(config)


def test_tls_problem_accepts_starttls_and_ldaps():
    starttls = ldap_auth.LdapConfig(server_uri='ldap://dir.test.invalid', start_tls=True)
    ldaps = ldap_auth.LdapConfig(server_uri='ldaps://dir.test.invalid', start_tls=False)
    assert ldap_auth.tls_problem(starttls) is None
    assert ldap_auth.tls_problem(ldaps) is None


def test_authenticate_rejects_empty_credentials_without_contacting_server():
    # Must short-circuit: a bind with an empty password is an unauthenticated
    # bind the directory would accept.
    config = ldap_auth.LdapConfig(
        server_uri='ldaps://unreachable.test.invalid',
        user_search_base='ou=people,dc=test,dc=invalid',
    )
    assert ldap_auth.authenticate('alice', '', config) is None
    assert ldap_auth.authenticate('', 'secret', config) is None
