import os
import sqlite3
import asyncio
from datetime import datetime, timezone

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

DB_FILE = os.getenv("DB_FILE", "log_guard.db")

# How far back we look for an Audit Log deletion.
AUDIT_MAX_AGE = 30

# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row

db.execute(
    """
    CREATE TABLE IF NOT EXISTS messages (
        message_id TEXT PRIMARY KEY,
        guild_id TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        author_id TEXT,
        author_name TEXT,
        content TEXT,
        created_at TEXT,
        saved_at TEXT
    )
    """
)

db.commit()


def save_message(message: discord.Message):
    try:
        db.execute(
            """
            INSERT OR REPLACE INTO messages
            (
                message_id,
                guild_id,
                channel_id,
                author_id,
                author_name,
                content,
                created_at,
                saved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(message.id),
                str(message.guild.id),
                str(message.channel.id),
                str(message.author.id),
                str(message.author),
                message.content,
                message.created_at.isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

        db.commit()

        print(
            f"💾 Saved message "
            f"{message.id}"
        )

    except Exception as e:
        print(
            f"❌ Database save error: {e}"
        )


def get_saved_message(message_id: int):
    try:
        cursor = db.execute(
            """
            SELECT *
            FROM messages
            WHERE message_id = ?
            """,
            (str(message_id),),
        )

        return cursor.fetchone()

    except Exception as e:
        print(
            f"❌ Database read error: {e}"
        )
        return None


def delete_saved_message(message_id: int):
    try:
        db.execute(
            """
            DELETE FROM messages
            WHERE message_id = ?
            """,
            (str(message_id),),
        )

        db.commit()

    except Exception as e:
        print(
            f"❌ Database delete error: {e}"
        )


# ============================================================
# BOT SETUP
# ============================================================

intents = discord.Intents.default()

intents.guilds = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)


# ============================================================
# DM ALERT
# ============================================================

async def send_alert(message: str):

    try:
        user = bot.get_user(ALERT_USER_ID)

        if user is None:
            user = await bot.fetch_user(
                ALERT_USER_ID
            )

        await user.send(message)

        print(
            "✅ Alert DM sent successfully."
        )

    except discord.Forbidden:
        print(
            "❌ Cannot DM alert user."
        )

    except discord.HTTPException as e:
        print(
            f"❌ Discord DM error: {e}"
        )

    except Exception as e:
        print(
            f"❌ Unexpected DM error: {e}"
        )


# ============================================================
# BOT READY
# ============================================================

@bot.event
async def on_ready():

    print("----------------------------------------")

    print(
        f"Logged in as "
        f"{bot.user} ({bot.user.id})"
    )

    print(
        f"Watching guild ID: "
        f"{GUILD_ID}"
    )

    print(
        f"Watching channel ID: "
        f"{LOG_CHANNEL_ID}"
    )

    print(
        f"Database: {DB_FILE}"
    )

    print("----------------------------------------")


# ============================================================
# SAVE EVERY MESSAGE IN PROTECTED LOG CHANNEL
# ============================================================

@bot.event
async def on_message(
    message: discord.Message
):

    # Ignore DMs.
    if message.guild is None:
        return

    # Only save messages in the protected
    # server-log channel.
    if (
        message.guild.id == GUILD_ID
        and message.channel.id == LOG_CHANNEL_ID
    ):

        save_message(message)

    await bot.process_commands(message)


# ============================================================
# FIND DELETER
# ============================================================

async def find_deleter(
    guild: discord.Guild,
    channel_id: int,
    original_author_id: int | None,
):
    """
    Discord's Audit Log for message deletion normally
    identifies the author of the deleted message as the
    target, while the moderator/bot who deleted it is
    entry.user.

    We therefore use:
      - recent timestamp
      - correct channel
      - original author when available

    If there isn't enough information to safely identify
    the person, we explicitly say so.
    """

    print(
        "🔎 Checking Audit Log..."
    )

    for attempt in range(8):

        try:

            await asyncio.sleep(1)

            now = discord.utils.utcnow()

            candidates = []

            async for entry in guild.audit_logs(
                limit=50,
                action=discord.AuditLogAction.message_delete,
            ):

                age = (
                    now - entry.created_at
                ).total_seconds()

                if age < 0:
                    continue

                if age > AUDIT_MAX_AGE:
                    continue

                extra = getattr(
                    entry,
                    "extra",
                    None,
                )

                audit_channel = getattr(
                    extra,
                    "channel",
                    None,
                )

                audit_channel_id = getattr(
                    audit_channel,
                    "id",
                    None,
                )

                target_id = getattr(
                    entry.target,
                    "id",
                    None,
                )

                print(
                    "🔎 AUDIT ENTRY | "
                    f"entry={entry.id} | "
                    f"user={entry.user} | "
                    f"target={target_id} | "
                    f"channel={audit_channel_id} | "
                    f"age={age:.2f}s"
                )

                # Must be our protected channel.
                if (
                    audit_channel_id
                    != channel_id
                ):
                    continue

                # Score candidates.
                score = 0

                # Stronger match if Discord's target
                # corresponds to the original message author.
                if (
                    original_author_id is not None
                    and target_id
                    == original_author_id
                ):
                    score += 100

                # More recent = better candidate.
                score += max(
                    0,
                    int(
                        AUDIT_MAX_AGE
                        - age
                    ),
                )

                candidates.append(
                    (
                        score,
                        entry,
                    )
                )

            if candidates:

                candidates.sort(
                    key=lambda x: x[0],
                    reverse=True,
                )

                best_score, best_entry = (
                    candidates[0]
                )

                # If we know the original author and
                # it matched, we have stronger evidence.
                if (
                    original_author_id is not None
                    and getattr(
                        best_entry.target,
                        "id",
                        None,
                    )
                    == original_author_id
                ):

                    print(
                        "🎯 Strong Audit Log match."
                    )

                    return (
                        f"{best_entry.user} "
                        f"({best_entry.user.id})",
                        True,
                    )

                # We found a recent deletion in the
                # correct channel, but cannot prove
                # the exact message.
                print(
                    "⚠️ Recent Audit Log entry found, "
                    "but exact deletion could not "
                    "be verified."
                )

                return (
                    f"{best_entry.user} "
                    f"({best_entry.user.id})",
                    False,
                )

        except discord.Forbidden:

            print(
                "❌ Cannot read Audit Log."
            )

            return (
                "⚠️ Could not verify — "
                "bot cannot read the Audit Log",
                False,
            )

        except discord.HTTPException as e:

            print(
                f"⚠️ Audit Log HTTP error: {e}"
            )

        except Exception as e:

            print(
                f"⚠️ Audit Log error: {e}"
            )

    print(
        "❌ No suitable Audit Log entry found."
    )

    return (
        "⚠️ Could not verify — "
        "Discord provided no matching Audit Log entry",
        False,
    )


# ============================================================
# MESSAGE DELETE
# ============================================================

@bot.event
async def on_raw_message_delete(
    payload: discord.RawMessageDeleteEvent
):

    print()
    print("========================================")
    print("🗑️ LOG MESSAGE DELETED")
    print(
        f"Message ID: {payload.message_id}"
    )
    print(
        f"Channel ID: {payload.channel_id}"
    )
    print("========================================")

    # Only monitor the protected log channel.
    if (
        payload.channel_id
        != LOG_CHANNEL_ID
    ):
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
        None,
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
            f"❌ Guild mismatch. "
            f"Expected {GUILD_ID}, "
            f"got {guild.id}"
        )

        return

    # --------------------------------------------------------
    # RECOVER SAVED LOG
    # --------------------------------------------------------

    saved = get_saved_message(
        payload.message_id
    )

    if saved:

        original_author_id = int(
            saved["author_id"]
        ) if saved["author_id"] else None

        original_author = (
            f'{saved["author_name"]} '
            f'({saved["author_id"]})'
        )

        deleted_content = (
            saved["content"]
            if saved["content"]
            else "(no text content)"
        )

        created_at = saved["created_at"]

        print(
            "💾 Original message recovered "
            "from SQLite."
        )

    else:

        original_author_id = None

        original_author = (
            "Unknown — message was not "
            "stored before deletion"
        )

        deleted_content = (
            "(content was not stored)"
        )

        created_at = "Unknown"

        print(
            "⚠️ Message was not found "
            "in SQLite."
        )

    # --------------------------------------------------------
    # FIND DELETER
    # --------------------------------------------------------

    deleter, verified = await find_deleter(
        guild=guild,
        channel_id=payload.channel_id,
        original_author_id=original_author_id,
    )

    # --------------------------------------------------------
    # VERIFICATION LABEL
    # --------------------------------------------------------

    if verified:

        deleter_label = (
            f"✅ {deleter}"
        )

    else:

        deleter_label = (
            f"⚠️ {deleter}"
        )

    # --------------------------------------------------------
    # TRIM CONTENT
    # --------------------------------------------------------

    if len(deleted_content) > 1500:

        deleted_content = (
            deleted_content[:1500]
            + "\n...[truncated]"
        )

    # --------------------------------------------------------
    # TIME
    # --------------------------------------------------------

    try:

        created_dt = datetime.fromisoformat(
            created_at
        )

        if created_dt.tzinfo is None:

            created_dt = created_dt.replace(
                tzinfo=timezone.utc
            )

        timestamp_text = (
            f"<t:{int(created_dt.timestamp())}:F>"
        )

    except Exception:

        timestamp_text = "Unknown"

    # --------------------------------------------------------
    # BUILD DM
    # --------------------------------------------------------

    alert = (
        "🚨 **LOG TAMPERING DETECTED**\n\n"

        f"**Server:** {guild.name}\n"
        f"**Channel:** #{channel.name}\n\n"

        f"**Deleted by:**\n"
        f"{deleter_label}\n\n"

        f"**Deleted log:**\n"
        f"{deleted_content}\n\n"

        f"**Original log author:**\n"
        f"{original_author}\n\n"

        f"**Message ID:**\n"
        f"`{payload.message_id}`\n\n"

        f"**Original log time:**\n"
        f"{timestamp_text}"
    )

    print()
    print(
        "🚨 Sending tampering alert..."
    )

    await send_alert(alert)

    # Remove the stored copy after the alert.
    delete_saved_message(
        payload.message_id
    )


# ============================================================
# BULK DELETE
# ============================================================

@bot.event
async def on_raw_bulk_message_delete(
    payload: discord.RawBulkMessageDeleteEvent
):

    if (
        payload.channel_id
        != LOG_CHANNEL_ID
    ):
        return

    channel = bot.get_channel(
        payload.channel_id
    )

    if channel is None:
        return

    guild = getattr(
        channel,
        "guild",
        None,
    )

    if guild is None:
        return

    if guild.id != GUILD_ID:
        return

    print(
        f"🚨 BULK DELETE: "
        f"{len(payload.message_ids)} messages"
    )

    # --------------------------------------------------------
    # Find bulk-delete Audit Log entry.
    # --------------------------------------------------------

    deleter = (
        "⚠️ Could not verify"
    )

    try:

        await asyncio.sleep(1)

        now = discord.utils.utcnow()

        async for entry in guild.audit_logs(
            limit=25,
            action=discord.AuditLogAction.message_bulk_delete,
        ):

            age = (
                now - entry.created_at
            ).total_seconds()

            if age < 0 or age > AUDIT_MAX_AGE:
                continue

            extra = getattr(
                entry,
                "extra",
                None,
            )

            audit_channel = getattr(
                extra,
                "channel",
                None,
            )

            audit_channel_id = getattr(
                audit_channel,
                "id",
                None,
            )

            if (
                audit_channel_id
                == LOG_CHANNEL_ID
            ):

                deleter = (
                    f"{entry.user} "
                    f"({entry.user.id})"
                )

                break

    except discord.Forbidden:

        deleter = (
            "⚠️ Could not verify — "
            "bot cannot read Audit Log"
        )

    except Exception as e:

        print(
            f"⚠️ Bulk Audit Log error: {e}"
        )

    # --------------------------------------------------------
    # Recover stored messages.
    # --------------------------------------------------------

    recovered = []

    for message_id in payload.message_ids:

        saved = get_saved_message(
            message_id
        )

        if saved:

            content = (
                saved["content"]
                if saved["content"]
                else "(no text content)"
            )

            if len(content) > 300:
                content = (
                    content[:300]
                    + "..."
                )

            recovered.append(
                f"`{message_id}` — {content}"
            )

            delete_saved_message(
                message_id
            )

    # Don't make a massive DM.
    if recovered:

        recovered_text = "\n".join(
            recovered[:15]
        )

        if len(recovered) > 15:

            recovered_text += (
                f"\n...and "
                f"{len(recovered) - 15} more."
            )

    else:

        recovered_text = (
            "(stored content unavailable)"
        )

    alert = (
        "🚨 **BULK LOG TAMPERING DETECTED**\n\n"

        f"**Server:** {guild.name}\n"
        f"**Channel:** #{channel.name}\n"
        f"**Messages deleted:** "
        f"{len(payload.message_ids)}\n\n"

        f"**Deleted by:**\n"
        f"{deleter}\n\n"

        f"**Deleted logs:**\n"
        f"{recovered_text}"
    )

    await send_alert(alert)


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
        "🚨 PROTECTED LOG CHANNEL DELETED!"
    )

    deleter = (
        "⚠️ Could not verify"
    )

    try:

        for _ in range(5):

            await asyncio.sleep(1)

            now = discord.utils.utcnow()

            async for entry in channel.guild.audit_logs(
                limit=25,
                action=discord.AuditLogAction.channel_delete,
            ):

                age = (
                    now - entry.created_at
                ).total_seconds()

                if age < 0 or age > AUDIT_MAX_AGE:
                    continue

                target_id = getattr(
                    entry.target,
                    "id",
                    None,
                )

                if target_id == channel.id:

                    deleter = (
                        f"{entry.user} "
                        f"({entry.user.id})"
                    )

                    break

            if not deleter.startswith(
                "⚠️"
            ):
                break

    except discord.Forbidden:

        deleter = (
            "⚠️ Could not verify — "
            "bot cannot read Audit Log"
        )

    except Exception as e:

        print(
            f"⚠️ Channel deletion "
            f"Audit Log error: {e}"
        )

    await send_alert(
        "🚨 **PROTECTED LOG CHANNEL DELETED**\n\n"
        f"**Server:** {channel.guild.name}\n"
        f"**Channel:** #{channel.name}\n"
        f"**Channel ID:** `{channel.id}`\n\n"
        f"**Deleted by:**\n"
        f"{deleter}\n\n"
        "⚠️ Your protected log channel "
        "was deleted."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    if not TOKEN:
        raise SystemExit(
            "❌ DISCORD_TOKEN is not set."
        )

    if not GUILD_ID:
        raise SystemExit(
            "❌ GUILD_ID is not set."
        )

    if not LOG_CHANNEL_ID:
        raise SystemExit(
            "❌ LOG_CHANNEL_ID is not set."
        )

    if not ALERT_USER_ID:
        raise SystemExit(
            "❌ ALERT_USER_ID is not set."
        )

    print(
        "🚀 Starting Log Guard..."
    )

    bot.run(TOKEN)
