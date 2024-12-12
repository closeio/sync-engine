"""Helper to reconfigure logging to Close.io needs."""

import logging


def reconfigure_logging() -> None:
    logging.getLogger("boto3").setLevel(logging.ERROR)
    logging.getLogger("botocore").setLevel(logging.ERROR)
    logging.getLogger("s3transfer").setLevel(logging.ERROR)
