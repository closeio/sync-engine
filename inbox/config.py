import errno
import os

import urllib3
import yaml

urllib3.disable_warnings()
from urllib3.exceptions import InsecureRequestWarning  # noqa: E402

urllib3.disable_warnings(InsecureRequestWarning)

__all__ = ["config"]


if "NYLAS_ENV" in os.environ:
    assert os.environ["NYLAS_ENV"] in (
        "dev",
        "test",
        "staging",
        "prod",
    ), "NYLAS_ENV must be either 'dev', 'test', staging, or 'prod'"
    env = os.environ["NYLAS_ENV"]
else:
    env = "prod"


def is_live_env() -> bool:
    return env in ["prod", "staging"]


class ConfigError(Exception):
    def __init__(  # type: ignore[no-untyped-def]
        self, error=None, help=None
    ) -> None:
        self.error = error or ""
        self.help = (
            help
            or "Run `sudo cp etc/config-dev.json /etc/inboxapp/config.json` and retry."
        )

    def __str__(self) -> str:
        return f"{self.error} {self.help}"


class Configuration(dict):  # type: ignore[type-arg]
    def __init__(  # type: ignore[no-untyped-def]
        self, *args, **kwargs
    ) -> None:
        dict.__init__(self, *args, **kwargs)

    def get_required(self, key):  # type: ignore[no-untyped-def]
        if key not in self:
            raise ConfigError(f"Missing config value for {key}.")

        return self[key]


def _update_config_from_env(config, env):  # type: ignore[no-untyped-def]
    """
    Update a config dictionary from configuration files specified in the
    environment.

    The environment variable `SYNC_ENGINE_CFG_PATH` contains a list of .json or .yml
    paths separated by colons.  The files are read in reverse order, so that
    the settings specified in the leftmost configuration files take precedence.
    (This is to emulate the behavior of the unix PATH variable, but the current
    implementation always reads all config files.)

    The following paths will always be appended:

    If `NYLAS_ENV` is 'prod':
      /etc/inboxapp/secrets.yml:/etc/inboxapp/config.json

    If `NYLAS_ENV` is 'test':
      {srcdir}/etc/secrets-test.yml:{srcdir}/etc/config-test.yml

    If `NYLAS_ENV` is 'dev':
      {srcdir}/etc/secrets-dev.yml:{srcdir}/etc/config-dev.yml

    Missing files in the path will be ignored.

    """
    srcdir = os.path.join(  # noqa: PTH118
        os.path.dirname(os.path.realpath(__file__)), ".."  # noqa: PTH120
    )

    if env in ["prod", "staging"]:
        base_cfg_path = [
            "/etc/inboxapp/secrets.yml",
            "/etc/inboxapp/config.json",
        ]
    else:
        v = {"env": env, "srcdir": srcdir}
        base_cfg_path = [
            "{srcdir}/etc/secrets-{env}.yml".format(**v),
            "{srcdir}/etc/config-{env}.json".format(**v),
        ]

    if "SYNC_ENGINE_CFG_PATH" in os.environ:
        cfg_path = os.environ.get("SYNC_ENGINE_CFG_PATH", "").split(
            os.path.pathsep
        )
        cfg_path = list(p.strip() for p in cfg_path if p.strip())
    else:
        cfg_path = []

    path = cfg_path + base_cfg_path

    for filename in reversed(path):
        try:
            with open(filename) as f:  # noqa: PTH123
                # this also parses json, which is a subset of yaml
                config.update(yaml.safe_load(f))
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise


def _update_config_from_env_variables(  # type: ignore[no-untyped-def]
    config,
) -> None:
    flags = (
        os.environ.get("FEATURE_FLAGS", "") or config.get("FEATURE_FLAGS", "")
    ).split()
    config["FEATURE_FLAGS"] = flags
    calendar_poll_frequencey = int(
        os.environ.get("CALENDAR_POLL_FREQUENCY", "")
        or config.get("CALENDAR_POLL_FREQUENCY", 300)
    )
    config["CALENDAR_POLL_FREQUENCY"] = calendar_poll_frequencey


def _get_process_name(config) -> None:  # type: ignore[no-untyped-def]
    if os.environ.get("PROCESS_NAME") is not None:
        config["PROCESS_NAME"] = os.environ.get("PROCESS_NAME")


config = Configuration()
_update_config_from_env(config, env)
_update_config_from_env_variables(config)
_get_process_name(config)
