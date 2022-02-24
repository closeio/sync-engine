import re
import socket
from urllib.parse import urlencode

import dns
from dns.resolver import NXDOMAIN, NoAnswer, NoNameservers, Resolver, Timeout
from tldextract import extract as tld_extract

from inbox.logging import get_logger

log = get_logger("inbox.util.url")

from inbox.providers import providers

# http://www.regular-expressions.info/email.html
EMAIL_REGEX = re.compile(r"[A-Z0-9._%+-]+@(?:[A-Z0-9-]+\.)+[A-Z]{2,4}", re.IGNORECASE)

# Use Google's Public DNS server (8.8.8.8)
GOOGLE_DNS_IP = "8.8.8.8"
dns_resolver = Resolver()
dns_resolver.nameservers = [GOOGLE_DNS_IP]


class InvalidEmailAddressError(Exception):
    pass


def _dns_resolver():
    return dns_resolver


def _fallback_get_mx_domains(domain):
    """
    Sometimes dns.resolver.Resolver fails to return what we want. See
    http://stackoverflow.com/questions/18898847. In such cases, try using
    dns.query.udp().

    """
    try:
        query = dns.message.make_query(domain, dns.rdatatype.MX)
        answers = dns.query.udp(query, GOOGLE_DNS_IP).answer[0]
        return [a for a in answers if a.rdtype == dns.rdatatype.MX]
    except Exception:
        return []


def get_mx_domains(domain, dns_resolver=_dns_resolver):
    """ Retrieve and return the MX records for a domain. """
    mx_records = []
    try:
        mx_records = dns_resolver().query(domain, "MX")
    except NoNameservers:
        log.error("NoMXservers", domain=domain)
    except NXDOMAIN:
        log.error("No such domain", domain=domain)
    except Timeout:
        log.error("Time out during resolution", domain=domain)
        raise
    except NoAnswer:
        log.error("No answer from provider", domain=domain)
        mx_records = _fallback_get_mx_domains(domain)

    return [str(rdata.exchange).lower() for rdata in mx_records]


def mx_match(mx_domains, match_domains):
    """
    Return True if any of the `mx_domains` matches an mx_domain
    in `match_domains`.

    """
    # convert legible glob patterns into real regexes
    match_domains = [
        d.replace(".", "[.]").replace("*", ".*") + "$" for d in match_domains
    ]
    for mx_domain in mx_domains:
        # Depending on how the MX server is configured, domain may
        # refer to a relative name or to an absolute one.
        # FIXME @karim: maybe resolve the server instead.
        if mx_domain[-1] == ".":
            mx_domain = mx_domain[:-1]

        # Match the given domain against any of the mx_server regular
        # expressions we have stored for the given domain. If none of them
        # match, then we cannot confirm this as the given provider
        def match_filter(x):
            return re.match(x, mx_domain)

        if any(match_filter(m) for m in match_domains):
            return True

    return False


def provider_from_address(email_address, dns_resolver=_dns_resolver):
    if not EMAIL_REGEX.match(email_address):
        raise InvalidEmailAddressError("Invalid email address")

    domain = email_address.split("@")[1].lower()
    mx_domains = get_mx_domains(domain, dns_resolver)
    ns_records = []
    try:
        ns_records = dns_resolver().query(domain, "NS")
    except NoNameservers:
        log.error("NoNameservers", domain=domain)
    except NXDOMAIN:
        log.error("No such domain", domain=domain)
    except Timeout:
        log.error("Time out during resolution", domain=domain)
    except NoAnswer:
        log.error("No answer from provider", domain=domain)

    for name, info in providers.items():
        provider_domains = info.get("domains", [])

        # If domain is in the list of known domains for a provider,
        # return the provider.
        for d in provider_domains:
            if domain.endswith(d):
                return name

    for name, info in providers.items():
        provider_mx = info.get("mx_servers", [])

        # If a retrieved mx_domain is in the list of stored MX domains for a
        # provider, return the provider.
        if mx_match(mx_domains, provider_mx):
            return name

    for name, info in providers.items():
        provider_ns = info.get("ns_servers", [])

        # If a retrieved name server is in the list of stored name servers for
        # a provider, return the provider.
        for rdata in ns_records:
            if str(rdata).lower() in provider_ns:
                return name

    return "unknown"


# From tornado.httputil
def url_concat(url, args, fragments=None):
    """
    Concatenate url and argument dictionary regardless of whether
    url has existing query parameters.

    >>> url_concat("http://example.com/foo?a=b", dict(c="d"))
    'http://example.com/foo?a=b&c=d'

    """
    if not args and not fragments:
        return url

    # Strip off hashes
    while url[-1] == "#":
        url = url[:-1]

    fragment_tail = ""
    if fragments:
        fragment_tail = "#" + urlencode(fragments)

    args_tail = ""
    if args:
        if url[-1] not in ("?", "&"):
            args_tail += "&" if ("?" in url) else "?"
        args_tail += urlencode(args)

    return url + args_tail + fragment_tail


def resolve_hostname(addr):
    try:
        return socket.gethostbyname(addr)
    except OSError:
        return None


def parent_domain(domain):
    return tld_extract(domain).registered_domain


def naked_domain(url):
    # This function extracts the domain name part of an URL.
    # It works indiscriminately on URLs or plain domains.
    res = tld_extract(url)

    if not res.subdomain or res.subdomain == "":
        return res.registered_domain
    else:
        return ".".join([res.subdomain, res.registered_domain])


def matching_subdomains(new_value, old_value):
    """
    We allow our customers to update their server addresses,
    provided that the new server has:
    1. the same IP as the old server
    2. shares the same top-level domain name.

    """

    if new_value is None and old_value is not None:
        return False

    if new_value.lower() == old_value.lower():
        return True

    new_domain = naked_domain(new_value)
    old_domain = naked_domain(old_value)

    if new_domain == old_domain:
        return True

    new_parent_domain = parent_domain(new_value)
    old_parent_domain = parent_domain(old_value)

    if old_parent_domain is None:
        log.error("old_parent_domain is None", old_value=old_value, new_value=new_value)
        # Shouldn't actually happen.
        return False

    if new_parent_domain is None:
        log.error("new_parent_domain is None", old_value=old_value, new_value=new_value)
        return False

    if new_parent_domain != old_parent_domain:
        log.error("Domains aren't matching", new_value=new_value, old_value=old_value)
        return False

    new_ip = resolve_hostname(new_value)
    old_ip = resolve_hostname(old_value)

    if new_ip is None or old_ip is None or new_ip != old_ip:
        log.error(
            "IP addresses aren't matching", new_value=new_value, old_Value=old_value
        )
        return False

    return True
