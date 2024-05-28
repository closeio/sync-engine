"""Helper to reconfigure logging to Close.io needs."""

import logging


def reconfigure_logging():
    logging.getLogger("boto").setLevel(logging.ERROR)
    logging.getLogger("boto3").setLevel(logging.ERROR)
    logging.getLogger("botocore").setLevel(logging.ERROR)
