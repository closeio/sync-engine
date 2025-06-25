# Sync Engine [![CircleCI](https://circleci.com/gh/closeio/sync-engine.svg?style=svg)](https://circleci.com/gh/closeio/sync-engine)

This is a fork of the Nylas [sync-engine project](https://github.com/nylas/sync-engine). 
For those looking for a hosted email syncing service, check out 
[their current offerings](https://www.nylas.com/).

### Installation and Setup

TODO: Add Docker steps

Auth an account via the commandline to start syncing:

    bin/inbox-auth ben.bitdiddle1861@gmail.com

The `inbox-auth` command will walk you through the process of obtaining an 
authorization token from Google or another service for syncing your mail. Your
credentials are stored to the local MySQL database for simplicity.

The sync engine will automatically begin syncing your account with the underlying provider. The `inbox-sync` command allows you to manually stop or restart the sync by running `inbox-sync stop [YOUR_ACCOUNT]@example.com` or `inbox-sync start [YOUR_ACCOUNT]@example.com`. Note that an initial sync can take quite a while depending on how much mail you have.

### API Service

The API service provides a REST API for interacting with your data. To start 
it in your development environment, run the command below:

```bash
$ bin/inbox-api
```

This will start the API Server on port 5555. At this point **You're now ready to make requests!** If you're using VirtualBox or VMWare fusion with Vagrant, port 5555 has already been forwarded to your host machine, so you can hit the API from your regular web browser.

You can get a list of all connected accounts by requesting `http://localhost:5555/accounts`. This endpoint requires no authentication.

For subsequent requests to retreive mail, contacts, and calendar data, your app should pass the `account_id` value from the previous step as the "username" parameter in HTTP Basic auth. For example:

```
curl --user 'ACCOUNT_ID_VALUE_HERE:' http://localhost:5555/threads
```

If you are using a web browser and would like to clear your cached HTTP Basic Auth values, simply visit http://localhost:5555/logout and click "Cancel".

## Security

For the sake of simplicity and setup speed, the development environment does 
not include any authentication or permission. For developing with sensitive 
data, we encourage developers to add their own protection, such as only 
running sync-engine on a local machine or behind a controlled firewall.
Note that passwords and OAuth tokens are stored unencrypted in the local MySQL data store on disk. This is intentional, for the same reason as above.


## Running tests

To run the test suite execute the following command: `docker-compose run app pytest`

## License

This code is free software, licensed under the The GNU Affero General Public License (AGPL). See the `LICENSE` file for more details.
