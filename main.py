import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
ALERT_USER_ID = int(os.getenv("ALERT_USER_ID", "0"))

# ============================================================
# BOT SETUP
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

# Store messages while the bot is online.
# This lets us include the original author/content when possible.
message_cache = {}


# ============================================================
# SEND DM ALERT
# ============================================================

async def send_alert(text: str):
    try:
        user = bot.get_user(ALERT_USER_ID)

        if user is None:
            user = await bot.fetch_user(ALERT_USER_ID)

        await user.send(text)

        print("✅ Alert DM sent successfully.")

    except discord.Forbidden:
        print(
            f"❌ Could not DM alert user {ALERT_USER_ID}. "
            "The user's DMs may be closed."
        )

    except discord.HTTPException as e:
        print(f"❌ Discord HTTP error while sending DM: {e}")

    except Exception as e:
        print(f"❌ Unexpected error while sending DM: {e}")


# ============================================================
# BOT READY
# ============================================================

@bot.event
async def on_ready():
    print("----------------------------------------")
    print(f"Logged in as {bot.user} ({bot.user.id})")
    print(f"Watching guild ID: {GUILD_ID}")
    print(f"Watching channel ID: {LOG_CHANNEL_ID}")
    print("----------------------------------------")


# ============================================================
# CACHE MESSAGES FROM LOG CHANNEL
# ============================================================

@bot.event
async def on_message(message: discord.Message):

    if (
        message.guild
        and message.guild.id == GUILD_ID
        and message.channel.id == LOG_CHANNEL_ID
    ):
        message_cache[message.id] = {
            "author": message.author,
            "content": message.content,
        }

        # Prevent unlimited memory usage.
        if len(message_cache) > 5000:
            oldest_id = next(iter(message_cache))
            del message_cache[oldest_id]

    await bot.process_commands(message)


# ============================================================
# FIND WHO DELETED A MESSAGE
# ============================================================

async def find_message_deleter(
    guild: discord.Guild,
    author_id: int,
    channel_id: int
):
    """
    Discord may take a moment to put the deletion into the Audit Log.
    Check several times before giving up.
    """

    for attempt in range(5):

        try:
            # Give Discord time to register the audit-log entry.
            await asyncio.sleep(1)

            async for entry in guild.audit_logs(
                limit=10,
                action=discord.AuditLogAction.message_delete
            ):

                # Ignore old audit-log entries.
                age = (
                    discord.utils.utcnow() - entry.created_at
                ).total_seconds()

                if age > 15:
                    continue

                target_id = getattr(
                    entry.target,
                    "id",
                    None
                )

                extra_channel = getattr(
                    entry.extra,
                    "channel",
                    None
                )

                extra_channel_id = getattr(
                    extra_channel,
                    "id",
                    None
                )

                if (
                    target_id == author_id
                    and extra_channel_id == channel_id
                ):
                    return f"{entry.user} ({entry.user.id})"

        except discord.Forbidden:
            print(
                "❌ Bot cannot read the Discord Audit Log."
            )

            return (
                "Unknown — bot lacks "
                "View Audit Log permission"
            )

        except discord.HTTPException as e:
            print(
                f"⚠️ Audit Log request failed: {e}"
            )

        except Exception as e:
            print(
                f"⚠️ Unexpected Audit Log error: {e}"
            )

    return (
        "Unknown — Discord did not provide "
        "a matching Audit Log entry"
    )


# ============================================================
# MESSAGE DELETED
# ============================================================

@bot.event
async def on_raw_message_delete(
    payload: discord.RawMessageDeleteEvent
):

    print(
        f"🗑️ DELETE EVENT: "
        f"{payload.message_id} "
        f"in {payload.channel_id}"
    )

    # Ignore every channel except your protected log channel.
    if payload.channel_id != LOG_CHANNEL_ID:
        return

    # --------------------------------------------------------
    # GET THE LOG CHANNEL
    # --------------------------------------------------------

    channel = bot.get_channel(payload.channel_id)

    if channel is None:

        try:
            channel = await bot.fetch_channel(
                payload.channel_id
            )

        except discord.NotFound:
            print("❌ Log channel no longer exists.")
            return

        except discord.Forbidden:
            print(
                "❌ Bot cannot access the log channel."
            )
            return

        except discord.HTTPException as e:
            print(
                f"❌ Could not fetch log channel: {e}"
            )
            return

    # --------------------------------------------------------
    # GET GUILD FROM CHANNEL
    # --------------------------------------------------------

    guild = getattr(channel, "guild", None)

    if guild is None:
        print(
            "❌ Could not determine the guild "
            "from the log channel."
        )
        return

    print(
        f"✅ Guild found: "
        f"{guild.name} ({guild.id})"
    )

    # Safety check.
    if guild.id != GUILD_ID:
        print(
            f"⚠️ Guild ID mismatch. "
            f"Expected {GUILD_ID}, got {guild.id}"
        )
        return

    # --------------------------------------------------------
    # GET CACHED MESSAGE INFORMATION
    # --------------------------------------------------------

    cached = message_cache.pop(
        payload.message_id,
        None
    )

    if cached:

        author = cached["author"]
        content = cached["content"]

        author_id = author.id
        author_text = (
            f"{author} ({author.id})"
        )

        if content:
            content_preview = content[:500]
        else:
            content_preview = "(no text content)"

    else:

        author_id = None

        author_text = (
            "Unknown — message was not cached"
        )

        content_preview = (
            "(message content unavailable)"
        )

    # --------------------------------------------------------
    # FIND DELETER
    # --------------------------------------------------------

    if author_id is not None:

        deleter = await find_message_deleter(
            guild=guild,
            author_id=author_id,
            channel_id=LOG_CHANNEL_ID
        )

    else:

        deleter = (
            "Unknown — original message "
            "was not cached"
        )

    print(
        f"🚨 Sending deletion alert. "
        f"Deleted by: {deleter}"
    )

    # --------------------------------------------------------
    # SEND ALERT
    # --------------------------------------------------------

    await send_alert(
        "⚠️ **LOG MESSAGE DELETED**\n\n"
        f"**Server:** {guild.name}\n"
        f"**Channel:** #{channel.name}\n"
        f"**Message ID:** `{payload.message_id}`\n"
        f"**Original author:** {author_text}\n"
        f"**Deleted by:** {deleter}\n"
        f"**Content:** {content_preview}"
    )


# ============================================================
# BULK MESSAGE DELETE
# ============================================================

@bot.event
async def on_raw_bulk_message_delete(
    payload: discord.RawBulkMessageDeleteEvent
):

    print(
        f"🚨 BULK DELETE EVENT: "
        f"{len(payload.message_ids)} messages "
        f"in {payload.channel_id}"
    )

    if payload.channel_id != LOG_CHANNEL_ID:
        return

    channel = bot.get_channel(
        payload.channel_id
    )

    if channel is None:
        try:
            channel = await bot.fetch_channel(
                payload.channel_id
            )
        except Exception as e:
            print(
                f"❌ Could not fetch channel: {e}"
            )
            return

    guild = getattr(channel, "guild", None)

    if guild is None:
        print(
            "❌ Could not determine guild "
            "for bulk deletion."
        )
        return

    # Remove deleted messages from cache.
    for message_id in payload.message_ids:
        message_cache.pop(
            message_id,
            None
        )

    await send_alert(
        "🚨 **BULK DELETE DETECTED**\n\n"
        f"**Server:** {guild.name}\n"
        f"**Channel:** #{channel.name}\n"
        f"**Messages deleted:** "
        f"{len(payload.message_ids)}\n\n"
        "⚠️ Multiple messages were deleted "
        "from the protected log channel."
    )


# ============================================================
# LOG CHANNEL DELETED
# ============================================================

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

    deleter = (
        "Unknown — could not determine "
        "from Audit Log"
    )

    # Give Discord a moment to register
    # the channel deletion in the Audit Log.
    await asyncio.sleep(1)

    try:

        async for entry in channel.guild.audit_logs(
            limit=10,
            action=discord.AuditLogAction.channel_delete
        ):

            target_id = getattr(
                entry.target,
                "id",
                None
            )

            if target_id == channel.id:

                deleter = (
                    f"{entry.user} "
                    f"({entry.user.id})"
                )

                break

    except discord.Forbidden:

        deleter = (
            "Unknown — bot lacks "
            "View Audit Log permission"
        )

    except discord.HTTPException as e:

        print(
            f"⚠️ Channel deletion audit error: {e}"
        )

    except Exception as e:

        print(
            f"⚠️ Unexpected channel deletion "
            f"audit error: {e}"
        )

    await send_alert(
        "🚨 **YOUR LOG CHANNEL WAS DELETED!**\n\n"
        f"**Server:** {channel.guild.name}\n"
        f"**Channel:** #{channel.name}\n"
        f"**Channel ID:** `{channel.id}`\n"
        f"**Deleted by:** {deleter}\n\n"
        "⚠️ The protected log channel "
        "no longer exists."
    )


# ============================================================
# START BOT
# ============================================================

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

    print("🚀 Starting Log Guard...")

    bot.run(TOKEN)
