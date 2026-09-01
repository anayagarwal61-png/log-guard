import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
ALERT_USER_ID = int(os.getenv("ALERT_USER_ID", "0"))

# --------------------------------------------------
# BOT SETUP
# --------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

# Small cache of messages seen while the bot is online.
# This lets us include message content/author when possible.
message_cache = {}


# --------------------------------------------------
# DM ALERT
# --------------------------------------------------

async def send_alert(text: str):
    try:
        user = bot.get_user(ALERT_USER_ID)

        if user is None:
            user = await bot.fetch_user(ALERT_USER_ID)

        await user.send(text)
        print("✅ Alert DM sent successfully.")

    except discord.Forbidden:
        print(
            f"❌ Could not DM user {ALERT_USER_ID}. "
            "Their DMs may be closed."
        )

    except discord.HTTPException as e:
        print(f"❌ Discord HTTP error while sending DM: {e}")

    except Exception as e:
        print(f"❌ Unexpected error while sending DM: {e}")


# --------------------------------------------------
# READY
# --------------------------------------------------

@bot.event
async def on_ready():
    print("----------------------------------------")
    print(f"Logged in as {bot.user} ({bot.user.id})")
    print(f"Watching guild ID: {GUILD_ID}")
    print(f"Watching channel ID: {LOG_CHANNEL_ID}")
    print("----------------------------------------")


# --------------------------------------------------
# CACHE MESSAGES
# --------------------------------------------------

@bot.event
async def on_message(message: discord.Message):
    # Only cache messages from the protected log channel.
    if (
        message.guild
        and message.guild.id == GUILD_ID
        and message.channel.id == LOG_CHANNEL_ID
    ):
        message_cache[message.id] = {
            "author": message.author,
            "content": message.content,
            "channel": message.channel,
        }

        # Keep cache from growing forever.
        if len(message_cache) > 5000:
            oldest_id = next(iter(message_cache))
            del message_cache[oldest_id]

    await bot.process_commands(message)


# --------------------------------------------------
# FIND WHO DELETED THE MESSAGE
# --------------------------------------------------

async def find_message_deleter(
    guild: discord.Guild,
    message_author_id: int,
    channel_id: int,
):
    """
    Discord's audit log can take a moment to update after a deletion,
    so we check several times.
    """

    for attempt in range(5):
        try:
            await asyncio.sleep(1.0 if attempt == 0 else 0.75)

            async for entry in guild.audit_logs(
                limit=10,
                action=discord.AuditLogAction.message_delete,
            ):
                # Make sure this audit entry is recent.
                age = (discord.utils.utcnow() - entry.created_at).total_seconds()

                if age < 15:
                    # Audit log target = author of deleted message
                    target_id = getattr(entry.target, "id", None)

                    # Audit log extra = channel where deletion happened
                    extra_channel = getattr(entry.extra, "channel", None)
                    extra_channel_id = getattr(extra_channel, "id", None)

                    if (
                        target_id == message_author_id
                        and extra_channel_id == channel_id
                    ):
                        return f"{entry.user} ({entry.user.id})"

        except discord.Forbidden:
            print("❌ Bot cannot read the Audit Log.")
            return "Unknown — bot lacks View Audit Log permission"

        except discord.HTTPException as e:
            print(f"⚠️ Audit Log request failed: {e}")

        except Exception as e:
            print(f"⚠️ Unexpected Audit Log error: {e}")

    return "Unknown — Discord did not provide a matching Audit Log entry"


# --------------------------------------------------
# SINGLE MESSAGE DELETE
# --------------------------------------------------

@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):

    print(
        f"🗑️ DELETE EVENT: "
        f"{payload.message_id} in {payload.channel_id}"
    )

    # Only watch the configured log channel.
    if payload.channel_id != LOG_CHANNEL_ID:
        return

    # Get the guild.
    guild = bot.get_guild(GUILD_ID)

    if guild is None:
        print("❌ Guild not found.")
        return

    # Try to get cached information about the deleted message.
    cached = message_cache.pop(payload.message_id, None)

    if cached:
        author = cached["author"]
        content = cached["content"]
        channel = cached["channel"]

        author_id = author.id
        author_text = f"{author} ({author.id})"

        if content:
            content_preview = content[:500]
        else:
            content_preview = "(no text content)"

        channel_name = channel.name

    else:
        # Message wasn't cached.
        author_id = 0
        author_text = "Unknown — message was not cached"
        content_preview = "(message content unavailable)"
        channel_name = "log channel"

    # Try to identify who deleted it.
    if author_id:
        deleter = await find_message_deleter(
            guild,
            author_id,
            LOG_CHANNEL_ID,
        )
    else:
        deleter = (
            "Unknown — cannot match Audit Log "
            "because the original message was not cached"
        )

    # Send alert.
    await send_alert(
        "⚠️ **LOG MESSAGE DELETED**\n\n"
        f"**Server:** {guild.name}\n"
        f"**Channel:** #{channel_name}\n"
        f"**Message ID:** `{payload.message_id}`\n"
        f"**Original author:** {author_text}\n"
        f"**Deleted by:** {deleter}\n"
        f"**Content:** {content_preview}"
    )


# --------------------------------------------------
# BULK DELETE
# --------------------------------------------------

@bot.event
async def on_raw_bulk_message_delete(
    payload: discord.RawBulkMessageDeleteEvent
):

    print(
        f"🚨 BULK DELETE EVENT: "
        f"{len(payload.message_ids)} messages in {payload.channel_id}"
    )

    if payload.channel_id != LOG_CHANNEL_ID:
        return

    guild = bot.get_guild(GUILD_ID)

    if guild is None:
        return

    # Remove deleted messages from our cache.
    for message_id in payload.message_ids:
        message_cache.pop(message_id, None)

    await send_alert(
        "🚨 **BULK DELETE DETECTED IN LOG CHANNEL**\n\n"
        f"**Server:** {guild.name}\n"
        f"**Channel ID:** `{LOG_CHANNEL_ID}`\n"
        f"**Messages deleted:** {len(payload.message_ids)}\n\n"
        "⚠️ This may indicate a purge or mass deletion."
    )


# --------------------------------------------------
# LOG CHANNEL ITSELF DELETED
# --------------------------------------------------

@bot.event
async def on_guild_channel_delete(
    channel: discord.abc.GuildChannel
):

    if channel.id != LOG_CHANNEL_ID:
        return

    if channel.guild.id != GUILD_ID:
        return

    print(
        f"🚨 LOG CHANNEL DELETED: "
        f"{channel.name} ({channel.id})"
    )

    deleter = "Unknown"

    # Give Discord a moment to register the audit entry.
    await asyncio.sleep(1)

    try:
        async for entry in channel.guild.audit_logs(
            limit=10,
            action=discord.AuditLogAction.channel_delete,
        ):
            target_id = getattr(entry.target, "id", None)

            if target_id == channel.id:
                deleter = f"{entry.user} ({entry.user.id})"
                break

    except discord.Forbidden:
        deleter = "Unknown — bot lacks View Audit Log permission"

    except Exception as e:
        print(f"⚠️ Channel deletion audit error: {e}")

    await send_alert(
        "🚨 **YOUR LOG CHANNEL WAS DELETED!**\n\n"
        f"**Channel:** #{channel.name}\n"
        f"**Channel ID:** `{channel.id}`\n"
        f"**Deleted by:** {deleter}\n\n"
        "⚠️ The protected log channel no longer exists."
    )


# --------------------------------------------------
# START
# --------------------------------------------------

if __name__ == "__main__":

    if not TOKEN:
        raise SystemExit(
            "DISCORD_TOKEN is not set."
        )

    if not GUILD_ID:
        raise SystemExit(
            "GUILD_ID is not set."
        )

    if not LOG_CHANNEL_ID:
        raise SystemExit(
            "LOG_CHANNEL_ID is not set."
        )

    if not ALERT_USER_ID:
        raise SystemExit(
            "ALERT_USER_ID is not set."
        )

    bot.run(TOKEN)
