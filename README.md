# Log Guard Bot

A tiny, single-purpose bot: it watches one specific channel (your log channel) and
DMs you immediately if:
- A message in it gets deleted (single delete)
- Multiple messages get deleted at once (bulk purge — usually more alarming)
- The channel itself gets deleted

It tries to identify who did it using Discord's Audit Log, but this only works
reliably if the deletion happened recently and the bot has permission to read the
audit log. If it can't determine who did it, it'll say "Unknown" rather than guess.

## Setup

1. Create a new bot application at the [Developer Portal](https://discord.com/developers/applications)
   — this needs to be its own separate bot, don't reuse Carry System, Gambling, or any other bot's token
2. Bot tab → Reset Token → copy it → enable **Server Members Intent** and **Message Content Intent**
3. OAuth2 → URL Generator → check `bot` → Permissions needed:
   - View Channels
   - Read Message History
   - **View Audit Log** (required for it to identify who deleted something)
4. Invite it to Void Runners with the generated URL
5. Copy `.env.example` to `.env` and fill in:
   - `DISCORD_TOKEN` — from step 2
   - `GUILD_ID` — your server ID
   - `LOG_CHANNEL_ID` — the channel ID of your log channel (the one you want protected)
   - `ALERT_USER_ID` — **your own** Discord user ID (right-click your own name → Copy User ID) — this is who gets DMed
6. `pip install -r requirements.txt` then `python main.py` to test
7. Deploy to Railway same as your other bots (new GitHub repo, new Railway project, same env variables, start command `python main.py`)

## Testing it

1. Send a test message in your log channel, then delete it — you should get a DM within a few seconds
2. **Careful with testing channel deletion** — don't actually delete your real log channel to test. If you want to test that specific alert, make a throwaway test channel, set `LOG_CHANNEL_ID` to that instead temporarily, delete it, confirm the DM arrives, then switch `LOG_CHANNEL_ID` back to your real log channel

## Important limits, be aware of these

- **This only protects the one channel ID you configure.** If the log channel gets recreated with a new ID (like if it's deleted and remade), you'll need to update `LOG_CHANNEL_ID` to the new one.
- **Audit log attribution isn't always available.** Discord's audit log for message deletions doesn't always clearly map to who did it, especially with bots deleting on someone's behalf (like Sapphire's automod). "Unknown" showing up sometimes is expected, not a bug.
- **This bot needs to stay running 24/7** on Railway, same as your others, or it can't alert you while offline.
- **This is a detection tool, not a prevention tool.** By the time you get the DM, the deletion already happened. For actual prevention, lock down channel permissions so only Owner/Co-Owner can delete anything in the log channel in the first place — do both together for real protection.
