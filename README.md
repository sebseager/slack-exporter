# slack-exporter

A Slack bot and standalone script for exporting messages and file attachments from public and private channels, using Slack's new Conversations API.

A similar service is provided by Slack for workspace admins at [https://my.slack.com/services/export](https://my.slack.com/services/export) (where `my` can be replaced with your full workspace name to refer to a workspace different than your default). However, it can only access public channels, while `slack-exporter` can retrieve data from any channel accessible to your user account.

## Authentication with Slack

There are two ways to use `slack-exporter` (detailed below). Both require a Slack API token to be able to communicate with your workspace.

1. Visit [https://api.slack.com/apps/](https://api.slack.com/apps/) and sign in to your workspace.
2. Click `Create New App`, enter a name (e.g., `Slack Exporter`), and select your workspace.
3. In the left-hand panel, navigate to `OAuth & Permissions`, and scroll to `User Token Scopes` (**not** `Bot Token Scopes`).
4. Select the following permissions: 
    - `channels:read`, `channels:history`
    - `groups:read`, `groups:history`
    - `mpim:read`, `mpim:history`
    - `im:read`, `im:history`
    - `users:read`
5. Select `Install to Workspace` at the top of that page (or `Reinstall to Workspace` if you have done this previously) and accept at the prompt.
6. Copy the `OAuth Access Token` (which will generally start with `xoxp` for user-level permissions)

## Usage

### As a standalone script

`exporter.py` can create an archive of all conversation history in your workspace which is accessible to your user account.

1. Either add 

    ```text
    SLACK_USER_TOKEN = xoxp-xxxxxxxxxxxxx...
    ```
    
    to a file named `.env` in the same directory as `exporter.py`, or run the following in your shell (replacing the value with the user token you obtained in the [Authentication with Slack](#authentication-with-slack) section above).

    ```shell script
    export SLACK_USER_TOKEN=xoxp-xxxxxxxxxxxxx...
    ```

2. Run `python exporter.py --help` to view the available export options.

### As a Slack bot

`bot.py` is a Slack bot that responds to "slash commands" in Slack channels (e.g., `/export-channel`). To connect the bot to the Slack app generated in [Authentication with Slack](#authentication-with-slack), create a file named `.env` in the root directory of this repo, and add the following line:

```text
SLACK_USER_TOKEN = xoxp-xxxxxxxxxxxxx...
``` 

Save this file and run the Flask application in `bot.py` such that the application is exposed to the Internet. This can be done via a web server (e.g., Heroku), as well as via the ngrok service, which assigns your `localhost` server a public URL.

To use the ngrok method:

1. [Download](https://ngrok.com/download) the appropriate binary.
2. Run `python bot.py`
3. Run the ngrok binary with `path/to/ngrok http 5000`, where `5000` is the port on which the Flask application (step 2) is running. Copy the forwarding HTTPS address provided.

Return to the Slack app you created in [Authentication with Slack](#authentication-with-slack) and navigate to the `Slash Commands` page in the sidebar. Create the following slash commands (one for each applicable Flask route in `bot.py`):

| Command         | Request URL                               | Arguments    | Example Usage        |
|-----------------|-------------------------------------------|--------------|----------------------|
| /export-channel | https://`[host_url]`/slack/export-channel | json \| text | /export-channel text |
| /export-replies | https://`[host_url]`/slack/export-replies | json \| text | /export-replies json |

where, if using ngrok, `[domain]` would be replaced with something like `https://xxxxxxxxxxxx.ngrok.io`.

Navigate back to `OAuth & Permissions` and click `(Re)install to Workspace` to add these slash commands to the workspace.
