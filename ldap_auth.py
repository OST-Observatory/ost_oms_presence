"""
LDAP authentication for the Observatory Presence dashboard.

Uses ldap3 (pure Python, no libldap/compiler needed). The hardening mirrors
the django-auth-ldap setup of ost_inventory:

  * TLS is never silently downgraded -- a failed STARTTLS aborts the bind.
  * Certificates are always verified (CERT_REQUIRED), optionally against a
    dedicated CA file.
  * Group membership understands posixGroup (memberUid), groupOfNames
    (member) and memberOf, so the same code works against different
    directory schemas.
"""

import logging
import os
import ssl

log = logging.getLogger(__name__)

DEFAULT_USER_FILTER = '(uid=%(user)s)'
USER_ATTRIBUTES = ['uid', 'cn', 'givenName', 'sn', 'mail', 'memberOf']
GROUP_ATTRIBUTES = ['member', 'memberUid']


def parse_bool(value, default=False):
    if value is None or value == '':
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def parse_group_dns(value):
    """Split the configured group list.

    Group DNs contain commas, so ';' (or a newline) separates entries.
    """
    if not value:
        return []
    parts = []
    for chunk in str(value).replace('\n', ';').split(';'):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts


def as_text(value):
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode('utf-8')
        except UnicodeDecodeError:
            return value.decode('utf-8', 'replace')
    return str(value)


def as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [as_text(v) for v in value]
    return [as_text(value)]


class LdapError(Exception):
    """Directory unreachable or misconfigured (as opposed to bad credentials)."""


class LdapConfig:
    def __init__(
        self,
        server_uri='',
        start_tls=True,
        tls_cacert='',
        bind_dn='',
        bind_password='',
        user_search_base='',
        user_filter=DEFAULT_USER_FILTER,
        allowed_group_dns=(),
        connect_timeout=5,
    ):
        self.server_uri = (server_uri or '').strip()
        self.start_tls = start_tls
        self.tls_cacert = (tls_cacert or '').strip()
        self.bind_dn = (bind_dn or '').strip()
        self.bind_password = bind_password or ''
        self.user_search_base = (user_search_base or '').strip()
        self.user_filter = (user_filter or '').strip() or DEFAULT_USER_FILTER
        self.allowed_group_dns = list(allowed_group_dns or [])
        self.connect_timeout = connect_timeout

    @property
    def uses_ldaps(self):
        return self.server_uri.lower().startswith('ldaps://')

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        try:
            timeout = int(env.get('LDAP_CONNECT_TIMEOUT', '5'))
        except ValueError:
            timeout = 5
        return cls(
            server_uri=env.get('LDAP_SERVER_URI', ''),
            start_tls=parse_bool(env.get('LDAP_START_TLS'), default=True),
            tls_cacert=env.get('LDAP_TLS_CACERT', ''),
            bind_dn=env.get('LDAP_BIND_DN', ''),
            bind_password=env.get('LDAP_BIND_PASSWORD', ''),
            user_search_base=env.get('LDAP_USER_SEARCH_BASE', ''),
            user_filter=env.get('LDAP_USER_FILTER', ''),
            allowed_group_dns=parse_group_dns(env.get('LDAP_ALLOWED_GROUP_DNS', '')),
            connect_timeout=timeout,
        )


def tls_problem(config):
    """Return a message when the transport would carry passwords in clear text.

    Mirrors config/security_checks.py:ldap_tls_is_required of ost_inventory:
    plain ldap:// without STARTTLS is refused.
    """
    if not config.server_uri:
        return None
    if config.uses_ldaps or config.start_tls:
        return None
    return (
        f'LDAP_SERVER_URI={config.server_uri} uses plain ldap:// without STARTTLS. '
        'User passwords would travel unencrypted. '
        'Use ldaps:// or set LDAP_START_TLS=1.'
    )


def dn_equal(a, b):
    """Case-insensitive DN comparison, ignoring surrounding whitespace.

    Not a full RFC 4514 normalisation, but directories return DNs
    consistently enough that this matches django-auth-ldap's behaviour.
    """
    return as_text(a).strip().lower() == as_text(b).strip().lower()


def matches_group(group_dn, user_dn, uid, member_of=(), group_attrs=None):
    """Decide membership from already-fetched attributes.

    Three schemas are accepted, in the order they are cheapest to check:
      1. the user's memberOf lists the group (AD, memberof overlay)
      2. the group's member lists the user DN (groupOfNames)
      3. the group's memberUid lists the uid (posixGroup)
    """
    for dn in member_of or ():
        if dn_equal(dn, group_dn):
            return True
    group_attrs = group_attrs or {}
    for member in as_list(group_attrs.get('member')):
        if dn_equal(member, user_dn):
            return True
    if uid:
        for member_uid in as_list(group_attrs.get('memberUid')):
            if member_uid == uid:
                return True
    return False


def _read_group(conn, group_dn):
    """Fetch member/memberUid of one group. Returns {} when unreadable."""
    from ldap3 import BASE

    try:
        ok = conn.search(
            search_base=group_dn,
            search_filter='(objectClass=*)',
            search_scope=BASE,
            attributes=GROUP_ATTRIBUTES,
        )
    except Exception:
        log.debug('LDAP group read failed for %s', group_dn, exc_info=True)
        return {}
    if not ok or not conn.response:
        return {}
    for entry in conn.response:
        attrs = entry.get('attributes') or {}
        if attrs:
            return attrs
    return {}


def user_in_any_group(conn, user_dn, uid, member_of, group_dns):
    """Return the configured group DNs the user belongs to."""
    matched = []
    for group_dn in group_dns:
        if matches_group(group_dn, user_dn, uid, member_of=member_of):
            matched.append(group_dn)
            continue
        # Only hit the directory when memberOf did not already answer it.
        group_attrs = _read_group(conn, group_dn)
        if matches_group(group_dn, user_dn, uid, member_of=(), group_attrs=group_attrs):
            matched.append(group_dn)
    return matched


def _connect(config, user=None, password=None):
    """Open a bound connection, or raise LdapError.

    Returns None when the credentials are rejected; raises LdapError when the
    directory cannot be reached or TLS cannot be established -- the caller
    must tell those two cases apart.
    """
    from ldap3 import Connection, Server, Tls

    tls = Tls(
        validate=ssl.CERT_REQUIRED,
        ca_certs_file=config.tls_cacert or None,
    )
    try:
        server = Server(
            config.server_uri,
            use_ssl=config.uses_ldaps,
            tls=tls,
            connect_timeout=config.connect_timeout,
        )
        conn = Connection(
            server,
            user=user or None,
            password=password or None,
            auto_bind=False,
            raise_exceptions=False,
            receive_timeout=config.connect_timeout,
        )
    except Exception as exc:
        raise LdapError(f'LDAP connection setup failed: {exc}') from exc

    try:
        if config.start_tls and not config.uses_ldaps:
            if not conn.start_tls():
                # Never fall back to a plaintext bind.
                raise LdapError('LDAP STARTTLS failed; refusing plaintext bind')
        if not conn.bind():
            conn.unbind()
            return None
    except LdapError:
        try:
            conn.unbind()
        except Exception:
            pass
        raise
    except Exception as exc:
        try:
            conn.unbind()
        except Exception:
            pass
        raise LdapError(f'LDAP bind failed: {exc}') from exc
    return conn


def _find_user(conn, config, username):
    """Return (dn, attributes) for the uid, or (None, None)."""
    from ldap3 import SUBTREE
    from ldap3.utils.conv import escape_filter_chars

    search_filter = config.user_filter.replace(
        '%(user)s', escape_filter_chars(username)
    )
    try:
        ok = conn.search(
            search_base=config.user_search_base,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=USER_ATTRIBUTES,
        )
    except Exception as exc:
        raise LdapError(f'LDAP user search failed: {exc}') from exc
    if not ok or not conn.response:
        return None, None
    for entry in conn.response:
        dn = entry.get('dn')
        if dn:
            return dn, (entry.get('attributes') or {})
    return None, None


def authenticate(username, password, config=None):
    """Verify credentials against the directory.

    Returns {'uid', 'display_name', 'groups'} on success, None when the
    credentials or the group membership are rejected. Raises LdapError when
    the directory itself is unreachable, so the caller can show
    "service unavailable" instead of "wrong password".
    """
    config = config or LdapConfig.from_env()
    username = (username or '').strip()

    # An LDAP bind with an empty password is an *unauthenticated bind*: the
    # server answers "success" without checking anything. Without this guard
    # any existing account could be taken over by submitting a blank password.
    if not username or not password:
        return None
    if not config.server_uri or not config.user_search_base:
        raise LdapError('LDAP is not configured (server URI or search base missing)')

    service_conn = _connect(config, config.bind_dn or None, config.bind_password or None)
    if service_conn is None:
        raise LdapError('LDAP service bind was rejected; check LDAP_BIND_DN/PASSWORD')

    try:
        user_dn, attrs = _find_user(service_conn, config, username)
        if not user_dn:
            log.info('LDAP login rejected: no directory entry for %r', username)
            return None

        user_conn = _connect(config, user_dn, password)
        if user_conn is None:
            log.info('LDAP login rejected: bad password for %r', username)
            return None
        try:
            user_conn.unbind()
        except Exception:
            pass

        uid_values = as_list(attrs.get('uid'))
        uid = uid_values[0] if uid_values else username
        member_of = as_list(attrs.get('memberOf'))

        groups = []
        if config.allowed_group_dns:
            groups = user_in_any_group(
                service_conn, user_dn, uid, member_of, config.allowed_group_dns
            )
            if not groups:
                log.info(
                    'LDAP login rejected: %r is in none of the allowed groups', username
                )
                return None

        cn_values = as_list(attrs.get('cn'))
        display_name = cn_values[0] if cn_values else uid
        return {'uid': uid, 'display_name': display_name, 'groups': groups}
    finally:
        try:
            service_conn.unbind()
        except Exception:
            pass
