#!/usr/bin/env python


import binascii

import nacl.secret
import nacl.utils
import yaml

from inbox.error_handling import maybe_enable_rollbar


def main() -> None:
    from inbox.config import config, secrets_path

    maybe_enable_rollbar()

    # If the config contains encryption keys, don't override.
    if config.get("SECRET_ENCRYPTION_KEY"):
        raise Exception(
            "Encryption keys already present in secrets config "
            f"file {secrets_path}"
        )

    # Generate keys
    data = {
        "SECRET_ENCRYPTION_KEY": binascii.hexlify(
            nacl.utils.random(nacl.secret.SecretBox.KEY_SIZE)
        ),
        "BLOCK_ENCRYPTION_KEY": binascii.hexlify(
            nacl.utils.random(nacl.secret.SecretBox.KEY_SIZE)
        ),
    }

    # Our secrets config file contains our database credentials etc.,
    # so it better exist.
    # Update it
    try:
        with open(secrets_path, "a") as f:  # noqa: PTH123
            print(f"Writing keys to secrets config file {secrets_path}")
            yaml.dump(data, f, default_flow_style=False)
    except OSError:
        raise Exception(  # noqa: B904
            f"Check file write permissions on config file {secrets_path}"
        )

    # Update the config dict
    config.update(data)


if __name__ == "__main__":
    main()
