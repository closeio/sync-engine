import re
import sys
from datetime import datetime
from email.utils import mktime_tz, parsedate_tz
from importlib import import_module

from inbox.providers import providers
from inbox.util.file import iter_module_names


class DummyContextManager:
    def __enter__(self):  # noqa: ANN204
        return None

    def __exit__(self, exc_type, exc_value, traceback):  # noqa: ANN204
        return False


class ProviderSpecificException(Exception):
    pass


def or_none(value, selector):  # noqa: ANN201
    if value is None:
        return None
    else:
        return selector(value)


def parse_ml_headers(headers):  # noqa: ANN201
    """
    Parse the mailing list headers described in RFC 4021,
    these headers are optional (RFC 2369).

    """
    return {
        "List-Archive": headers.get("List-Archive"),
        "List-Help": headers.get("List-Help"),
        "List-Id": headers.get("List-Id"),
        "List-Owner": headers.get("List-Owner"),
        "List-Post": headers.get("List-Post"),
        "List-Subscribe": headers.get("List-Subscribe"),
        "List-Unsubscribe": headers.get("List-Unsubscribe"),
    }


def parse_references(references: str, in_reply_to: str) -> list[str]:
    """
    Parse a References: header and returns an array of MessageIDs.
    The returned array contains the MessageID in In-Reply-To if
    the header is present.

    Parameters
    ----------
    references: string
        the contents of the referfences header

    in_reply_to: string
        the contents of the in-reply-to header

    Returns
    -------
    list of MessageIds (strings) or an empty list.

    """
    replyto = in_reply_to.split()[0] if in_reply_to else in_reply_to

    if not references:
        if replyto:
            return [replyto]
        else:
            return []

    reference_list = references.split()
    if replyto not in reference_list:
        reference_list.append(replyto)

    return reference_list


def dt_to_timestamp(dt):  # noqa: ANN201
    return int((dt - datetime(1970, 1, 1)).total_seconds())


def get_internaldate(date: str | None, received: str | None) -> datetime:
    """Get the date from the headers."""
    if date is None:
        assert received
        _, date = received.split(";")

    # All in UTC
    parsed_date = parsedate_tz(date)
    assert parsed_date
    timestamp = mktime_tz(parsed_date)
    dt = datetime.utcfromtimestamp(timestamp)

    return dt


# Based on: http://stackoverflow.com/a/8556471
def load_modules(base_name, base_path):  # noqa: ANN201
    """
    Imports all modules underneath `base_module` in the module tree.

    Returns
    -------
    list
        All the modules in the base module tree.

    """  # noqa: D401
    modules = []

    for module_name in iter_module_names(base_path):
        full_module_name = f"{base_name}.{module_name}"

        module = sys.modules.get(
            full_module_name, import_module(full_module_name)
        )
        modules.append(module)

    return modules


def register_backends(base_name, base_path):  # noqa: ANN201
    """
    Dynamically loads all packages contained within thread
    backends module, including those by other module install paths

    """
    modules = load_modules(base_name, base_path)

    mod_for = {}
    for module in modules:
        if hasattr(module, "PROVIDER"):
            provider_name = module.PROVIDER
            if provider_name == "generic":
                for p_name, p in providers.items():
                    p_type = p.get("type", None)
                    if p_type == "generic" and p_name not in mod_for:
                        mod_for[p_name] = module
            else:
                mod_for[provider_name] = module

    return mod_for


def cleanup_subject(subject_str):  # noqa: ANN201
    """
    Clean-up a message subject-line, including whitespace.
    For instance, 'Re: Re: Re: Birthday   party' becomes 'Birthday party'
    """
    if subject_str is None:
        return ""
    # TODO consider expanding to all
    # http://en.wikipedia.org/wiki/List_of_email_subject_abbreviations
    prefix_regexp = r"(?i)^((re|fw|fwd|aw|wg|undeliverable|undelivered):\s*)+"
    subject = re.sub(prefix_regexp, "", subject_str)

    whitespace_regexp = r"\s+"
    return re.sub(whitespace_regexp, " ", subject)


# IMAP doesn't support nested folders and instead encodes paths inside folder
# names.
# imap_folder_path converts a "/" delimited path to an IMAP compatible path.
def imap_folder_path(path, separator=".", prefix=""):  # noqa: ANN201
    folders = [folder for folder in path.split("/") if folder != ""]

    res = None

    if folders:
        res = separator.join(folders)

        if prefix and not res.startswith(prefix):
            # Check that the value we got for the prefix doesn't include
            # the separator too (i.e: `INBOX.` instead of `INBOX`).
            if prefix[-1] != separator:
                res = f"{prefix}{separator}{res}"
            else:
                res = f"{prefix}{res}"

    return res


def strip_prefix(path, prefix):  # noqa: ANN201
    if path.startswith(prefix):
        return path[len(prefix) :]

    return path


# fs_folder_path converts an IMAP compatible path to a "/" delimited path.
def fs_folder_path(path, separator=".", prefix=""):  # noqa: ANN201
    if prefix:
        path = strip_prefix(path, prefix)

    folders = path.split(separator)
    # Remove stray '' which can happen if the folder is prefixed
    # i.e: INBOX.Taxes.Accounting -> .Taxes.Accounting -> ['', 'Taxes', 'Accounting']
    if folders[0] == "":
        folders.pop(0)

    return "/".join(folders)
