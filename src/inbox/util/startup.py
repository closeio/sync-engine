# XXX(dlitz): Most of this is deployment-related stuff that belongs outside the
# main Python invocation.
import json
import os
import sys
import time

from inbox.config import config
from inbox.logging import get_logger

log = get_logger()


def check_sudo() -> None:
    env = os.getenv("NYLAS_ENV", "prod")
    # Don't run the Nylas Sync Engine as root in production or staging.
    # Ideally env would be attached to config so we don't use os.environ
    # outside of config.py. However, os.environ is used in error_handler.py
    # as well, so, for now, this is the best place to check if the current
    # environment is production or staging.
    if os.getuid() == 0 and env in ["prod", "staging"]:
        raise Exception("Don't run the Nylas Sync Engine as root!")


# TODO(menno) - It's good to have all servers use UTC for general
# sanity, but the IMAPClient concern mentioned in the warning text
# below can be avoided. When an IMAPClient instance's
# `normalise_times` attribute is set to False IMAPClient will return
# unnormalised, timezone-aware timestamps. Changing this is probably
# non-trival because timezone and non-timezone-aware timestamps don't
# mix. Some care will be required to ensure that only timezone-aware
# timestamps are used where they might mix with timestamps originating
# from IMAPClient.

_TZ_ERROR_TEXT = """
WARNING!

System time is not set to UTC! This is a problem because
imapclient will normalize INTERNALDATE responses to the 'local'
timezone. \n\nYou can fix this by running

$ echo 'UTC' | sudo tee /etc/timezone

and then checking that it worked with

$ sudo dpkg-reconfigure --frontend noninteractive tzdata

"""


def check_tz() -> None:
    if time.tzname[time.daylight] not in ["UTC", "GMT"]:
        sys.exit(_TZ_ERROR_TEXT)


def load_overrides(  # type: ignore[no-untyped-def]
    file_path, loaded_config=config
) -> None:
    """
    Convenience function for overriding default configuration.

    file_path : <string> the full path to a file containing valid
                JSON for configuration overrides
    """  # noqa: D401
    with open(file_path) as data_file:  # noqa: PTH123
        try:
            overrides = json.load(data_file)
        except ValueError:
            sys.exit(f"Failed parsing configuration file at {file_path}")
        if not overrides:
            log.debug("No config overrides found.")
            return
        assert isinstance(overrides, dict), "overrides must be dictionary"
        loaded_config.update(overrides)
        log.debug(f"Imported config overrides {list(overrides)}")


def preflight() -> None:
    check_sudo()
    check_tz()

    # Print a traceback when the process receives signal SIGSEGV, SIGFPE,
    # SIGABRT, SIGBUS or SIGILL
    import faulthandler

    faulthandler.enable()
