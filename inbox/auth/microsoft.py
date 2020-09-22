import attr


@attr.s
class MicrosoftAccountData(object):
    email = attr.ib()

    secret_type = attr.ib()
    secret_value = attr.ib()

    client_id = attr.ib()

    sync_email = attr.ib()
