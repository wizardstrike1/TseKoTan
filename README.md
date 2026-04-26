# TseKoTan Discord Bot

Slash-command Discord bot with:

- `/whitelist [user]`: add a user to the **command whitelist**
- `/addping [role]`: add a server role to the **ping-role whitelist**
- `/rallydm [message]`: DM a message to **all members who have any whitelisted role**

Only users in the **command whitelist** can run *any* commands (including `/whitelist`).  
Your account is hardcoded/forced into the command whitelist: `528953104939483186`.

## Setup

1. Create a virtualenv (optional) and install deps:

```bash
pip install -r requirements.txt
```

2. Copy `config.example.env` to `.env` and fill in your bot token:

- `DISCORD_BOT_TOKEN=` (required)

3. Run:

```bash
python bot.py
```

## Notes on “DM online members only”

If you set `DM_ONLY_ONLINE=true` in `.env`, the bot will *attempt* to DM only members whose presence is not offline.

To make this work reliably you must enable the privileged intent:

- Discord Developer Portal → Your App → Bot → **Presence Intent** ON

Also note:

- Discord may not always provide accurate presence for all members, especially in large guilds.

## Storage

`storage.json` contains:

- `user_whitelist`: user IDs allowed to run commands
- `ping_role_whitelist`: role IDs whose members will be targeted by `/rallydm`

