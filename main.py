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

# Messages seen while the bot is online.
# Used to recover the original author/content after deletion.
message_cache = {}

# Audit-log entries already used for an alert.
# Prevents accidentally attributing the same deletion twice.
used_audit_entries = set()


# ============================================================
# SEND DM
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
            "❌ Could not DM the alert user. "
            "Their DMs may be closed."
        )

    except discord.HTTPException as e:
        print(f"❌ Discord HTTP error while sending DM: {e}")

    except Exception as e:
        print(f"❌ Unexpected DM error: {e}")


# ============================================================
# READY
# ============================================================

@bot.event
async def on_ready():
    print("----------------------------------------")
    print(f"Logged in as {bot.user} ({bot.user.id})")
    print(f"Watching guild ID: {GUILD_ID}")
    print(f"Watching channel ID: {LOG_CHANNEL_ID}")
    print("----------------------------------------")


# ============================================================
# CACHE LOG MESSAGES
# ============================================================

@bot.event
async def on_message(message: discord.Message):

    if (
        message.guild is not None
        and message.guild.id == GUILD_ID
        and message.channel.id == LOG_CHANNEL_ID
    ):

        message_cache[message.id] = {
            "author_id": message.author.id,
            "author_name": str(message.author),
            "content": message.content,
            "created_at": message.created_at,
        }

        # Keep memory under control.
        if len(message_cache) > 5000:
            oldest_id = next(iter(message_cache))
            del message_cache[oldest_id]

    await bot.process_commands(message)


# ============================================================
# FIND DELETER
# ============================================================

async def find_message_deleter(
    guild: discord.Guild,
    message_author_id: int,
    channel_id: int,
    message_created_at
):
    """
    Try to match the deletion with Discord's Audit Log.

    Discord can take a moment to create the audit-log entry,
    so we check several times.

    Matching factors:
      1. Audit action = message delete
      2. Correct channel
      3. Correct original message author
      4. Recent audit entry
      5. Entry has not already been used
    """

    for attempt in range(6):

        try:

            # First check happens after a short delay.
            # Later attempts give Discord additional time.
            await asyncio.sleep(
                0.8 if attempt == 0 else 1.0
            )

            now = discord.utils.utcnow()

            async for entry in guild.audit_logs(
                limit=25,
                action=discord.AuditLogAction.message_delete
            ):

                # ------------------------------------------------
                # Don't reuse an audit entry.
                # ------------------------------------------------

                if entry.id in used_audit_entries:
                    continue

                # ------------------------------------------------
                # Only consider very recent entries.
                # ------------------------------------------------

                age = (
                    now - entry.created_at
                ).total_seconds()

                if age < 0 or age > 20:
                    continue

                # ------------------------------------------------
                # Get original message author from audit entry.
                # ------------------------------------------------

                target_id = getattr(
                    entry.target,
                    "id",
                    None
                )

                if target_id != message_author_id:
                    continue

                # ------------------------------------------------
                # Get deletion channel from audit entry.
                # ------------------------------------------------

                extra = getattr(
                    entry,
                    "extra",
                    None
                )

                audit_channel = getattr(
                    extra,
                    "channel",
                    None
                )

                audit_channel_id = getattr(
                    audit_channel,
                    "id",
                    None
                )

                if audit_channel_id != channel_id:
                    continue

                # ------------------------------------------------
                # We found a strong match.
                # ------------------------------------------------

                used_audit_entries.add(entry.id)

                deleter = entry.user

                print(
                    "✅ AUDIT MATCH: "
                    f"{deleter} ({deleter.id}) "
                    f"| entry={entry.id} "
                    f"| channel={audit_channel_id}"
                )

                return (
                    f"{deleter} ({deleter.id})"
                )

        except discord.Forbidden:
            print(
                "❌ Cannot read Audit Log. "
                "Check View Audit Log permission."
            )

            return (
                "Unknown — bot cannot read "
                "the Audit Log"
            )

        except discord.HTTPException as e:
            print(
                f"⚠️ Audit Log request failed: {e}"
            )

        except Exception as e:
            print(
                f"⚠️ Audit Log matching error: {e}"
            )

    print(
        "⚠️ No matching Audit Log entry found."
    )

    return (
        "Unknown — Discord did not provide "
        "a matching Audit Log entry"
    )


# ============================================================
# SINGLE MESSAGE DELETE
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

    # Only protect the configured log channel.
    if payload.channel_id != LOG_CHANNEL_ID:
        return

    # --------------------------------------------------------
    # Get channel
    # --------------------------------------------------------

    channel = bot.get_channel(
        payload.channel_id
    )

    if channel is None:

        try:
            channel = await bot.fetch_channel(
                payload.channel_id
            )

        except discord.NotFound:
            print(
                "❌ Log channel no longer exists."
            )
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
    # Get guild directly from channel
    # --------------------------------------------------------

    guild = getattr(
        channel,
        "guild",
        None
    )

    if guild is None:
        print(
            "❌ Could not determine guild "
            "from log channel."
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
            f"Expected {GUILD_ID}, "
            f"got {guild.id}"
        )
        return

    # --------------------------------------------------------
    # Recover cached message information
    # --------------------------------------------------------

    cached = message_cache.pop(
        payload.message_id,
        None
    )

    if cached:

        author_id = cached["author_id"]
        author_name = cached["author_name"]
        content = cached["content"]
        message_created_at = cached["created_at"]

        author_text = (
            f"{author_name} ({author_id})"
        )

        if content:
            content_preview = content[:500]
        else:
            content_preview = (
                "(no text content)"
            )

    else:

        author_id = None
        message_created_at = None

        author_text = (
            "Unknown — message was not cached"
        )

        content_preview = (
            "(message content unavailable)"
        )

    # --------------------------------------------------------
    # Find who deleted it
    # --------------------------------------------------------

    if author_id is not None:

        deleter = await find_message_deleter(
            guild=guild,
            message_author_id=author_id,
            channel_id=LOG_CHANNEL_ID,
            message_created_at=message_created_at
        )

    else:

        deleter = (
            "Unknown — original message "
            "was not cached"
        )

    # --------------------------------------------------------
    # Send alert
    # --------------------------------------------------------

    print(
        f"🚨 Sending deletion alert. "
        f"Deleted by: {deleter}"
    )

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
# BULK DELETE
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

    guild = getattr(
        channel,
        "guild",
        None
    )

    if guild is None:
        print(
            "❌ Could not determine guild."
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

    # Give Discord time to record the deletion.
    await asyncio.sleep(1)

    try:

        async for entry in channel.guild.audit_logs(
            limit=25,
            action=discord.AuditLogAction.channel_delete
        ):

            target_id = getattr(
                entry.target,
                "id",
                None
            )

            age = (
                discord.utils.utcnow()
                - entry.created_at
            ).total_seconds()

            if (
                target_id == channel.id
                and 0 <= age <= 20
            ):

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
# START
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
