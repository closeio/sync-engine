# Sync Engine [![CircleCI](https://circleci.com/gh/closeio/sync-engine.svg?style=svg)](https://circleci.com/gh/closeio/sync-engine)

This is a fork of the Nylas [sync-engine project](https://github.com/nylas/sync-engine).
For those looking for a hosted email syncing service, check out
[their current offerings](https://www.nylas.com/).


## Installation and Setup

> [!NOTE]
> To install sync-engine, you will need to have installed the required system packages for the
> mysqlclient package. See: https://github.com/PyMySQL/mysqlclient?tab=readme-ov-file#linux

> [!NOTE]
> The development environment uses [uv](https://docs.astral.sh/uv/) to manage dependencies and
> the virtual environment. Thus, any commands being run need to be prefixed with `uv run` so that
> the correct version of python is being used in the prepared virtual environment.


### Setup with docker compose

The steps below will guide you through the process of setting up the `sync-engine` locally with
docker compose.


#### Build the app container

You will need to run a few commands to prepare the environment before you can start the API. These
commands will be run in the app container, so it needs to be built first:

```bash
docker compose build app
```


#### Initialize the database

Before you can go any further, you need to initialize the MySQL  database that sync-engine depends
on. Run the `create-db` executable to prepare the database:

```bash
docker compose run app uv run bin/create-db.py
```


#### Authorize your user

> [!NOTE]
> In order to connect your user, you need to make sure that the app is correctly configured with a
> Google OAuth2 client that can authorize requests to access user information. See
> [Configuring an OAuth2 Client](#configuring-an-oauth2-client) below if you need to set this up.

Next, you need to add your user to the `sync-engine` app. To do this, run the following command
to start syncing:

```bash
docker compose run app uv run bin/inbox-auth <your-email>
```

The `inbox-auth` prints url that you can use to authorize access. And asks you to enter an
authorization code.

First, copy the URL into a browser, and follow the steps to log in and authorize access. After you
are finished, Google will attempt to redirect you to `localhost`. This won't load, but that's no
problem. Just copy the `code` segment from the url. It will look something like:

```
4/0AQSTgQFmHiPypxwsfHOEK1IL8o-ZI4S7Jnc8Vl_UKyOA78DPdrUrW81mIX6hm-TGlJFf-w
```

Paste this into the terminal as the authorization code. Your user will now be added to `sync-engine`.
Your credentials are stored to the local MySQL database for simplicity. The sync engine will
automatically begin syncing your account with the underlying provider. The `inbox-sync` command
allows you to manually stop the sync by running:

```bash
inbox-sync stop <your-email>
```

You can restart the sync process by running:

```bash
inbox-sync start <your-email>
```

> [!NOTE]
> An initial sync can take quite a while depending on how much mail you have!



#### Run the API

Next, use docker compose to bring up the API service to begin interacting with your data:

```bash
docker compose run app uv run bin/inbox-api
```

This will start the API Server on port 5555. At this point **You're now ready to make requests!**

You can get a list of all connected accounts by requesting `http://localhost:5555/accounts`.
This endpoint requires no authentication.

For subsequent requests to retreive mail, contacts, and calendar data, your app should pass the
`account_id` value from the previous step as the "username" parameter in HTTP Basic auth.

For example:

```
curl --user 'ACCOUNT_ID_VALUE_HERE:' http://localhost:5555/threads
```

If you are using a web browser and would like to clear your cached HTTP Basic Auth values, simply
visit http://localhost:5555/logout and click "Cancel".


### Setup without docker compose

Auth an account via the commandline to start syncing:

    bin/inbox-auth ben.bitdiddle1861@gmail.com

The `inbox-auth` command will walk you through the process of obtaining an
authorization token from Google or another service for syncing your mail. Your credentials are stored to the local MySQL database for simplicity.

The sync engine will automatically begin syncing your account with the underlying provider. The `inbox-sync` command allows you to manually stop or restart the sync by running `inbox-sync stop [YOUR_ACCOUNT]@example.com` or `inbox-sync start [YOUR_ACCOUNT]@example.com`. Note that an initial sync can take quite a while depending on how much mail you have.

#### API Service

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

> [!NOTE]
> Passwords and OAuth tokens are stored unencrypted in the local MySQL data store on disk. This is
intentional, for the same reason as above.


## Running tests

To run the test suite execute the following command: `docker-compose run app uv run pytest`


## Configuring an OAuth2 Client

If you need to set up an OAuth2 client with Google for testing, follow these steps:

### Sign into the Google cloud console

Navigate to the [Google Cloud Console](http://console.cloud.google.com/) and sign in with the user
that you will be using to test.


### Create a project

If you don't already have a Google Cloud project, you will need to create one. After logging in,
click the `Select a project` button at the top of the page. In the modal that pops up, click the
`New Project` button in the top-right.

Choose a project name that is meaningful for you, then click the `Create` button.


### Navigate to APIs & services

Next, you need to open the `APIs & Services` dashboard.


### Configure Consent Screen

If you created a new project or just haven't configured the consent screen yet, you will need to
set this up now.

Find and click on the `OAuth consent screen` section in the side-nav. Click the `GET STARTED` button
in the middle of the page.

Choose a name for your app. It doesn't matter what you use as long as it's meaningful to you.

Select your email for the `User support email` selector.

Select `External` for your audience unless you are a part of an organization and want to limit sign-in
to users in your organization.

Enter your email address again under `Contact Information` and click the `Next` button.

Agree to Google's User Data Policy and click the `CONTINUE` button.

Finally, click the `CREATE` button to finish. Now, navigate back to `APIs & Services`.


### Create an OAuth2 Client ID

Find the `Credentials` section on the side-nav and open it.

Now, you will need to create a new Client ID. Find the `+ CREATE CREDENTIALS` button near the top-center of the page and
click it.

Select `OAuth client ID` from the drop-down list.

For `Application Type`, select "Web application".

It doesn't matter what you name the application, so just use something that's meaningful to you.

Next, you need to create a redirect URI. Under `Authorized redirect URIs`, click the `+ ADD URI`
button. Enter "http://localhost".

Finally, click the `CREATE` button at the bottom. You can dismiss the modal with your Client ID and
Client Secret, because you can access them later.


### Add a test user

Now, click on the new Oauth 2.0 Client ID that you just created. This will navigate you to the
"Google Auth Platform" page for your client.

From here, click on the `Audience` section of the side-nav.

Under the `Test users` section, click on the `+ ADD USERS` button. Here you can add any test users
that you want to be able to sign-in with this client. Enter at least your own email address here
and click `SAVE`.


### Configure sync-engine with your Client ID

Now, you are ready to configure the app to use your new Client ID. First, navigate to `CLIENTS` in
the side-nav and open the client you created.

Copy the `Client ID` and `Client secret` (they are found on the right panel).

In the `sync-engine` project, open the `etc/secrets-docker-dev.yml` in an editor. Replace the
`GOOGLE_OAUTH_CLIENT_ID` value with the Client ID from the Google Cloud Console. Then, replace
the `GOOGLE_OAUTH_CLIENT_SECRET` with the Client Secret from the console as well.

> [!CAUTION]
> Do not commit your changes to this file (`secrets-docker-dev.yml`) or your Google OAuth2 client
> will be exposed to the whole world!

Now, you are ready to [Authorize your user](#authorize-your-user) with your new OAuth2 client
and start up the inbox API!


## License

This code is free software, licensed under the The GNU Affero General Public License (AGPL). See the `LICENSE` file for more details.
