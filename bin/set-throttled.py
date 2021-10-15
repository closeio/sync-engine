#!/usr/bin/env python
# throttle or unthrottle an account
import optparse
import sys
import time

from inbox.error_handling import maybe_enable_rollbar
from inbox.models.account import Account
from inbox.models.session import session_scope


def print_usage():
    print "usage:   set-throttled [--throttled|--unthrottled] --id 1000"
    print "example: set-throttled --throttled --id 1000"
    print "batch usage: set-throttled also accepts tab-separated input on stdin."
    print "             echo 'karim@nylas.com	account_id' | set-throttled --throttled"
    print "             bin/list-accounts --host precise64 --paying | set-throttled --unthrottled"


def throttle(options):
    account_id = int(options.account_id)
    with session_scope(account_id) as db_session:
        if options.account_id:
            account = db_session.query(Account).get(account_id)
        else:
            print_usage()
            sys.exit(-1)

        if options.throttled:
            print "Throttling account %s" % account.email_address
            account.throttled = True
        elif options.unthrottled:
            print "Unthrottling account %s" % account.email_address
            account.throttled = False

        db_session.commit()


def main():
    parser = optparse.OptionParser()
    parser.add_option('--throttled', action="store_true", default=False)
    parser.add_option('--unthrottled', action="store_true", default=False)
    parser.add_option('--id', action="store", dest="account_id", default=None)
    parser.add_option('--stdin', action="store_true", default=False)
    options, remainder = parser.parse_args(sys.argv[1:])
    if all(opt is False for opt in [options.throttled, options.unthrottled]):
        print_usage()
        sys.exit(-1)

    maybe_enable_rollbar()

    # If we were not given the --stdin param, only start/stop the account
    # specified on the command-line.
    if not options.stdin:
        throttle(options)
    # Otherwise read from stdin.
    else:
        for line in sys.stdin:
            splat = line.split()
            if len(splat) < 2:
                continue

            email, id = splat[:2]
            options.account_id = id
            throttle(options)

if __name__ == '__main__':
    main()
