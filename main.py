import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
ALERT_USER_ID = int(os.getenv("ALERT_USER_ID", "0"))  # who gets DMed — usually you

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)


async def send_alert(text: str):
    user = bot.get_user(ALERT_USER_ID) or await bot.fetch_user(ALERT_USER_ID)
    if user:
        try:
            await user.send(text)
        except discord.Forbidden:
            print(f"Could not DM alert user {ALERT_USER_ID} — their DMs may be closed.")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    print(f"Watching channel ID: {LOG_CHANNEL_ID}")


@bot.event
async def on_message_delete(message: discord.Message):
    print(f"🗑️ DELETE EVENT: {message.id} in {message.channel.id}")

    if message.channel.id != LOG_CHANNEL_ID:
        return
    if message.guild is None or message.guild.id != GUILD_ID:
        return

    # Try to find who deleted it via the audit log (only works if the bot has
    # "View Audit Log" permission, and only reflects recent actions).
    deleter_text = "Unknown (could not determine from audit log)"
    try:
        async for entry in message.guild.audit_logs(limit=5, action=discord.AuditLogAction.message_delete):
            if entry.target.id == message.author.id and entry.extra.channel.id == LOG_CHANNEL_ID:
                deleter_text = f"{entry.user} ({entry.user.id})"
                break
    except discord.Forbidden:
        deleter_text = "Unknown (bot lacks View Audit Log permission)"

    content_preview = message.content[:300] if message.content else "(no text content — may have been an embed)"

    await send_alert(
        f"⚠️ **Log channel message deleted!**\n"
        f"Channel: #{message.channel.name} ({message.channel.id})\n"
        f"Original author: {message.author} ({message.author.id})\n"
        f"Likely deleted by: {deleter_text}\n"
        f"Content preview: {content_preview}"
    )


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    if channel.id != LOG_CHANNEL_ID:
        return
    if channel.guild.id != GUILD_ID:
        return

    deleter_text = "Unknown (could not determine from audit log)"
    try:
        async for entry in channel.guild.audit_logs(limit=5, action=discord.AuditLogAction.channel_delete):
            if entry.target.id == channel.id:
                deleter_text = f"{entry.user} ({entry.user.id})"
                break
    except discord.Forbidden:
        deleter_text = "Unknown (bot lacks View Audit Log permission)"

    await send_alert(
        f"🚨 **YOUR LOG CHANNEL WAS DELETED!**\n"
        f"Channel: #{channel.name} ({channel.id})\n"
        f"Deleted by: {deleter_text}\n"
        f"This is serious — the log channel itself is gone."
    )


@bot.event
async def on_bulk_message_delete(messages: list):
    if not messages:
        return
    if messages[0].channel.id != LOG_CHANNEL_ID:
        return
    await send_alert(
        f"🚨 **Bulk delete detected in log channel!**\n"
        f"Channel: #{messages[0].channel.name}\n"
        f"{len(messages)} messages were deleted at once — this looks like a purge, not a single accidental delete."
    )


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
    if not LOG_CHANNEL_ID or not ALERT_USER_ID:
        raise SystemExit("LOG_CHANNEL_ID and ALERT_USER_ID must be set in .env")
    bot.run(TOKEN)
