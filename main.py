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
# BOT
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# DM ALERT
# ============================================================

async def send_alert(text: str):
    try:
        user = bot.get_user(ALERT_USER_ID)

        if user is None:
            user = await bot.fetch_user(ALERT_USER_ID)

        await user.send(text)

        print("✅ Alert DM sent successfully.")

    except discord.Forbidden:
        print("❌ Cannot DM the alert user.")

    except discord.HTTPException as e:
        print(f"❌ Discord DM error: {e}")

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
# FIND WHO DELETED THE MESSAGE
# ============================================================

async def find_deleter(
    guild: discord.Guild,
    channel_id: int
):
    """
    Look directly at the Discord Audit Log.

    We intentionally DO NOT require the deleted message
    to have been cached.

    Discord does not expose the deleted message's author
    through the raw delete event, so the main goal here is
    identifying the moderator/user responsible for the
    deletion.
    """

    # Discord can take a short moment to create the audit entry.
    for attempt in range(6):

        try:
            await asyncio.sleep(1)

            now = discord.utils.utcnow()

            async for entry in guild.audit_logs(
                limit=25,
                action=discord.AuditLogAction.message_delete
            ):

                # Ignore old audit entries.
                age = (
                    now - entry.created_at
                ).total_seconds()

                if age < 0 or age > 20:
                    continue

                # ------------------------------------------------
                # Check the channel from the audit entry.
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
                # We found a recent deletion in the correct
                # channel.
                # ------------------------------------------------

                deleter = entry.user

                print(
                    "✅ AUDIT LOG MATCH FOUND"
                )

                print(
                    f"   Deleter: "
                    f"{deleter} ({deleter.id})"
                )

                print(
                    f"   Entry ID: {entry.id}"
                )

                print(
                    f"   Channel ID: "
                    f"{audit_channel_id}"
                )

                return (
                    f"{deleter} ({deleter.id})"
                )

        except discord.Forbidden:
            print(
                "❌ Cannot read Audit Log."
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
                f"⚠️ Audit Log error: {e}"
            )

    print(
        "⚠️ No matching Audit Log entry found."
    )

    return (
        "Unknown — Discord did not provide "
        "a matching Audit Log entry"
    )


# ============================================================
# MESSAGE DELETE
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

    # --------------------------------------------------------
    # ONLY WATCH SERVER-LOGS
    # --------------------------------------------------------

    if payload.channel_id != LOG_CHANNEL_ID:
        return

    # --------------------------------------------------------
    # GET CHANNEL
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # GET GUILD
    # --------------------------------------------------------

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

    print(
        f"✅ Guild found: "
        f"{guild.name} ({guild.id})"
    )

    # Safety check.
    if guild.id != GUILD_ID:
        print(
            f"⚠️ Guild mismatch."
            f" Expected {GUILD_ID},"
            f" got {guild.id}"
        )
        return

    # --------------------------------------------------------
    # FIND DELETER
    # --------------------------------------------------------

    deleter = await find_deleter(
        guild,
        payload.channel_id
    )

    # --------------------------------------------------------
    # ALERT
    # --------------------------------------------------------

    print(
        f"🚨 Sending deletion alert."
    )

    await send_alert(
        "⚠️ **LOG MESSAGE DELETED**\n\n"
        f"**Server:** {guild.name}\n"
        f"**Channel:** #{channel.name}\n"
        f"**Message ID:** `{payload.message_id}`\n"
        f"**Deleted by:** {deleter}\n\n"
        "ℹ️ The deleted message's original "
        "content/author may be unavailable "
        "because Discord does not provide it "
        "with the raw deletion event."
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
        return

    if guild.id != GUILD_ID:
        return

    # --------------------------------------------------------
    # Try to identify who performed the bulk deletion.
    # --------------------------------------------------------

    deleter = "Unknown"

    try:

        await asyncio.sleep(1)

        now = discord.utils.utcnow()

        async for entry in guild.audit_logs(
            limit=25,
            action=discord.AuditLogAction.message_bulk_delete
        ):

            age = (
                now - entry.created_at
            ).total_seconds()

            if age < 0 or age > 20:
                continue

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

            if audit_channel_id == LOG_CHANNEL_ID:

                deleter = (
                    f"{entry.user} "
                    f"({entry.user.id})"
                )

                break

    except discord.Forbidden:

        deleter = (
            "Unknown — bot cannot read "
            "the Audit Log"
        )

    except Exception as e:

        print(
            f"⚠️ Bulk deletion audit error: {e}"
        )

    await send_alert(
        "🚨 **BULK DELETE DETECTED**\n\n"
        f"**Server:** {guild.name}\n"
        f"**Channel:** #{channel.name}\n"
        f"**Messages deleted:** "
        f"{len(payload.message_ids)}\n"
        f"**Deleted by:** {deleter}\n\n"
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

    deleter = "Unknown"

    # Give Discord time to update Audit Log.
    await asyncio.sleep(1)

    try:

        now = discord.utils.utcnow()

        async for entry in channel.guild.audit_logs(
            limit=25,
            action=discord.AuditLogAction.channel_delete
        ):

            age = (
                now - entry.created_at
            ).total_seconds()

            if age < 0 or age > 20:
                continue

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

    except Exception as e:

        print(
            f"⚠️ Channel deletion audit error: {e}"
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
