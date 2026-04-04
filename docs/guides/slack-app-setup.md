# Slack App Setup Guide

This guide explains how to create and configure a Slack app for Tarka to enable alert notifications and inbound chat.

## Features

- **Notifications**: Tarka posts triage reports to a Slack channel when an alert is classified as `actionable` or `informational`.
- **Inbound chat**: Mention `@tarka` in a thread (or DM it) to ask follow-up questions about an investigation.

## Prerequisites

- Slack workspace admin access (to create and install an app)
- A running Tarka deployment to configure with the resulting credentials

## Step 1: Create the Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App → From scratch**.
2. Name it `Tarka` (or `tarka-<your-cluster>`) and select your workspace.
3. Click **Create App**.

## Step 2: Enable Socket Mode

Tarka uses Socket Mode (WebSocket), so no public HTTP endpoint is required.

1. Go to **Settings → Socket Mode** and toggle **Enable Socket Mode** ON.
2. When prompted, name the app-level token (e.g. `tarka-socket`) and click **Generate**.
3. Copy the `xapp-...` token — this is your `SLACK_APP_TOKEN`.

## Step 3: Add Bot Token Scopes

Go to **Features → OAuth & Permissions → Scopes → Bot Token Scopes** and add:

| Scope | Purpose |
|---|---|
| `app_mentions:read` | Receive `@tarka` mentions in channels |
| `chat:write` | Post messages and thread replies |
| `channels:read` | Resolve channel IDs |
| `users:read` | Read user profile information |
| `reactions:write` | Add emoji reactions for in-progress feedback |

## Step 4: Subscribe to Events

1. Go to **Features → Event Subscriptions** and toggle **Enable Events** ON.
2. Under **Subscribe to bot events**, add:
   - `app_mention` — triggered when someone `@tarka`s in a channel
   - `message.channels` — triggered on messages in public channels (required for thread replies without @mention)
   - `message.im` — triggered on direct messages to the bot
3. No Request URL is needed — Socket Mode handles delivery.

## Step 5: Set the Bot Avatar and Name

Give Tarka its identity in Slack — this can't be done via the API and must be configured in the app settings.

1. Go to **Basic Information → Display Information**.
2. Set **App name** to `Tarka`.
3. Under **App icon & Preview**, upload the Tarka robot icon (the blue robot on a light-blue circle background — export it from the UI or use the image from the Tarka console).
4. Optionally set a **Background color** to match Tarka's brand blue (`#135BEC`).
5. Click **Save Changes**.

The bot name color shown in Slack messages (like the purple in incident.io) is derived from the app's accent color set here.

## Step 6: Install the App

1. Go to **Settings → Install App** and click **Install to Workspace → Allow**.
2. Copy the **Bot User OAuth Token** (`xoxb-...`) — this is your `SLACK_BOT_TOKEN`.

## Step 7: Configure Environment Variables

Add the following to your deployment:

```bash
# Required for notifications
SLACK_BOT_TOKEN=xoxb-...
SLACK_DEFAULT_CHANNEL=#sre-alerts   # default channel for alert notifications

# Required for inbound chat (@tarka mentions and DMs)
SLACK_APP_TOKEN=xapp-...
```

Both features auto-enable when the relevant variables are present — no extra feature flags needed.

### Kubernetes Secret (recommended)

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: tarka-slack
  namespace: tarka
type: Opaque
stringData:
  bot-token: "xoxb-..."
  app-token: "xapp-..."
```

```yaml
# In your Deployment spec
env:
  - name: SLACK_BOT_TOKEN
    valueFrom:
      secretKeyRef:
        name: tarka-slack
        key: bot-token
  - name: SLACK_APP_TOKEN
    valueFrom:
      secretKeyRef:
        name: tarka-slack
        key: app-token
  - name: SLACK_DEFAULT_CHANNEL
    value: "#sre-alerts"
```

## Per-Alert Channel Routing

Individual Prometheus alerts can override the default channel by adding a `slack_channel` label:

```yaml
# In your Prometheus alert rule
labels:
  slack_channel: "#platform-alerts"
```

## Behaviour Notes

- Only alerts classified as `actionable` or `informational` trigger notifications. `noisy` and `artifact` alerts are silently skipped.
- If PostgreSQL is configured, Tarka stores thread mappings so that chat replies in a notification thread are scoped to the correct investigation.
- The bot must be invited to any private channel it should post in (`/invite @tarka`).

## Troubleshooting

### Bot doesn't post notifications

- Verify `SLACK_BOT_TOKEN` and `SLACK_DEFAULT_CHANNEL` are set.
- Confirm the bot is a member of the channel (invite it with `/invite @tarka`).
- Check Tarka logs for `slack` errors on alert completion.

### `@tarka` mentions get no response

- Verify `SLACK_APP_TOKEN` is set and Socket Mode is enabled in the app settings.
- Confirm the `app_mention`, `message.channels`, and `message.im` events are subscribed.
- Check Tarka logs for `SocketModeHandler` startup messages.

### Thread replies get no response (only first @mention works)

- The `message.channels` event subscription is required for thread replies in channels. Add it under **Features → Event Subscriptions → Subscribe to bot events** and reinstall the app.

### "channel_not_found" error

- The `channels:read` scope is required to resolve channel names to IDs. Confirm it is added and the app is reinstalled after any scope changes.

## Security Best Practices

1. Store tokens in Kubernetes Secrets, not ConfigMaps or environment variable literals.
2. Rotate tokens if they are accidentally exposed (revoke and regenerate in the app settings).
3. Restrict the bot to only the channels it needs — do not add it to every channel by default.

## Further Reading

- [Slack Bolt for Python](https://slack.dev/bolt-python/)
- [Slack Socket Mode](https://api.slack.com/apis/connections/socket)
- [Tarka Environment Variables](environment-variables.md)
- [Tarka Features: Chat](../features/chat.md)
