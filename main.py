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

# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row

db.execute("""
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    guild_id TEXT,
    channel_id TEXT,
    author_id TEXT,
    author_name TEXT,
    content TEXT,
    embeds TEXT,
    created_at TEXT,
    saved_at TEXT
)
""")

# Add embeds column if an older database already exists
try:
    db.execute("ALTER TABLE messages ADD COLUMN embeds TEXT")
    db.commit()
except sqlite3.OperationalError:
    pass

db.commit()


def save_message(message: discord.Message):
    """Save the complete log message, including embeds."""

    try:
        embed_text = []

        for embed in message.embeds:
            parts = []

            if embed.title:
                parts.append(f"Title: {embed.title}")

            if embed.description:
                parts.append(
                    f"Description: {embed.description}"
                )

            for field in embed.fields:
                parts.append(
                    f"{field.name}: {field.value}"
                )

            if embed.footer and embed.footer.text:
                parts.append(
                    f"Footer: {embed.footer.text}"
                )

            if parts:
                embed_text.append(
                    "\n".join(parts)
                )

        all_embeds = "\n\n".join(embed_text)

        db.execute("""
        INSERT OR REPLACE INTO messages
        (
            message_id,
            guild_id,
            channel_id,
            author_id,
            author_name,
            content,
            embeds,
            created_at,
            saved_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(message.id),
            str(message.guild.id),
            str(message.channel.id),
            str(message.author.id),
            str(message.author),
            message.content,
            all_embeds,
            message.created_at.isoformat(),
            datetime.now(timezone.utc).isoformat()
        ))

        db.commit()

        print(
            f"💾 Saved message {message.id} "
            f"(embeds={len(message.embeds)})"
        )

    except Exception as e:
        print(f"❌ Database save error: {e}")


def get_saved_message(message_id: int):
    try:
        cursor = db.execute("""
        SELECT *
        FROM messages
        WHERE message_id = ?
        """, (str(message_id),))

        return cursor.fetchone()

    except Exception as e:
        print(f"❌ Database read error: {e}")
        return None


def delete_saved_message(message_id: int):
    try:
        db.execute(
            "DELETE FROM messages WHERE message_id = ?",
            (str(message_id),)
        )
        db.commit()
    except Exception as e:
        print(f"❌ Database delete error: {e}")


# ============================================================
# BOT
# ============================================================

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# DM
# ============================================================

async def send_alert(text: str):

    try:
        user = bot.get_user(ALERT_USER_ID)

        if user is None:
            user = await bot.fetch_user(ALERT_USER_ID)

        await user.send(text)

        print("✅ Alert DM sent successfully.")

    except discord.Forbidden:
        print("❌ Cannot DM alert user.")

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

    print(
        f"Logged in as {bot.user} "
        f"({bot.user.id})"
    )

    print(
        f"Watching guild ID: {GUILD_ID}"
    )

    print(
        f"Watching channel ID: {LOG_CHANNEL_ID}"
    )

    print(
        f"Database: {DB_FILE}"
    )

    print("----------------------------------------")


# ============================================================
# SAVE LOG MESSAGES
# ============================================================

@bot.event
async def on_message(message: discord.Message):

    if message.guild is None:
        return

    if (
        message.guild.id == GUILD_ID
        and message.channel.id == LOG_CHANNEL_ID
    ):
        save_message(message)

    await bot.process_commands(message)


# ============================================================
# FIND WHO DELETED IT
# ============================================================

async def find_deleter(
    guild: discord.Guild,
    channel_id: int,
    original_author_id: int | None
):

    print("🔎 Checking Audit Log...")

    # Discord audit logs can take a moment to appear.
    for attempt in range(5):

        try:

            await asyncio.sleep(1)

            now = discord.utils.utcnow()

            candidates = []

            async for entry in guild.audit_logs(
                limit=50,
                action=discord.AuditLogAction.message_delete
            ):

                age = (
                    now - entry.created_at
                ).total_seconds()

                if age < 0 or age > 30:
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

                target_id = getattr(
                    entry.target,
                    "id",
                    None
                )

                print(
                    f"🔎 AUDIT ENTRY | "
                    f"user={entry.user} | "
                    f"target={target_id} | "
                    f"channel={audit_channel_id} | "
                    f"age={age:.1f}s"
                )

                if audit_channel_id != channel_id:
                    continue

                score = 0

                # Strong match when Discord's audit
                # target is the original message author.
                if (
                    original_author_id is not None
                    and target_id == original_author_id
                ):
                    score += 100

                # Prefer newer entries.
                score += max(
                    0,
                    int(30 - age)
                )

                candidates.append(
                    (score, entry)
                )

            if candidates:

                candidates.sort(
                    key=lambda x: x[0],
                    reverse=True
                )

                score, entry = candidates[0]

                if (
                    original_author_id is not None
                    and getattr(
                        entry.target,
                        "id",
                        None
                    ) == original_author_id
                ):
                    return (
                        f"{entry.user} "
                        f"({entry.user.id})",
                        True
                    )

                return (
                    f"{entry.user} "
                    f"({entry.user.id})",
                    False
                )

        except discord.Forbidden:

            return (
                "Discord Audit Log unavailable "
                "(missing View Audit Log permission)",
                False
            )

        except Exception as e:

            print(
                f"⚠️ Audit Log error: {e}"
            )

    return (
        "Unknown — no matching Audit Log entry",
        False
    )


# ============================================================
# MESSAGE DELETED
# ============================================================

@bot.event
async def on_raw_message_delete(
    payload: discord.RawMessageDeleteEvent
):

    if payload.channel_id != LOG_CHANNEL_ID:
        return

    print()
    print("========================================")
    print("🗑️ LOG MESSAGE DELETED")
    print(
        f"Message ID: {payload.message_id}"
    )
    print("========================================")

    # --------------------------------------------------------
    # CHANNEL
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

    guild = getattr(
        channel,
        "guild",
        None
    )

    if guild is None:
        print("❌ Guild not found.")
        return

    if guild.id != GUILD_ID:
        print(
            f"❌ Guild mismatch. "
            f"Expected {GUILD_ID}, "
            f"got {guild.id}"
        )
        return

    # --------------------------------------------------------
    # RECOVER ORIGINAL LOG
    # --------------------------------------------------------

    saved = get_saved_message(
        payload.message_id
    )

    if saved:

        original_author_id = (
            int(saved["author_id"])
            if saved["author_id"]
            else None
        )

        original_author = (
            f'{saved["author_name"]} '
            f'({saved["author_id"]})'
        )

        content = saved["content"] or ""
        embeds = saved["embeds"] or ""

        # Prefer embed information because
        # Sapphire/other logging bots commonly
        # put their actual logs inside embeds.
        if embeds.strip():

            deleted_log = embeds

        elif content.strip():

            deleted_log = content

        else:

            deleted_log = "(empty log)"

        created_at = saved["created_at"]

        print(
            "💾 Original log recovered."
        )

    else:

        original_author_id = None

        original_author = (
            "Unknown — message was not "
            "stored before deletion"
        )

        deleted_log = (
            "⚠️ Original log unavailable "
            "(bot was not running when it was created)"
        )

        created_at = None

        print(
            "⚠️ Original log was not stored."
        )

    # --------------------------------------------------------
    # FIND DELETER
    # --------------------------------------------------------

    deleter, verified = await find_deleter(
        guild,
        payload.channel_id,
        original_author_id
    )

    if verified:
        deleter_display = (
            f"✅ {deleter}"
        )
    else:
        deleter_display = (
            f"⚠️ {deleter}"
        )

    # --------------------------------------------------------
    # LIMIT MESSAGE SIZE
    # --------------------------------------------------------

    if len(deleted_log) > 3000:

        deleted_log = (
            deleted_log[:3000]
            + "\n...[truncated]"
        )

    # --------------------------------------------------------
    # TIME
    # --------------------------------------------------------

    if created_at:

        try:

            dt = datetime.fromisoformat(
                created_at
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=timezone.utc
                )

            time_text = (
                f"<t:{int(dt.timestamp())}:F>"
            )

        except Exception:

            time_text = "Unknown"

    else:

        time_text = "Unknown"

    # --------------------------------------------------------
    # DM
    # --------------------------------------------------------

    alert = (
        "🚨 **LOG TAMPERING DETECTED**\n\n"

        f"**Server:** {guild.name}\n"
        f"**Channel:** #{channel.name}\n\n"

        f"**WHO DELETED IT:**\n"
        f"{deleter_display}\n\n"

        f"**WHAT WAS DELETED:**\n"
        f"```text\n"
        f"{deleted_log}\n"
        f"```\n\n"

        f"**Log generated by:**\n"
        f"{original_author}\n\n"

        f"**Message ID:**\n"
        f"`{payload.message_id}`\n\n"

        f"**Original log time:**\n"
        f"{time_text}"
    )

    print(
        "🚨 Sending deletion alert."
    )

    await send_alert(alert)

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

    if payload.channel_id != LOG_CHANNEL_ID:
        return

    channel = bot.get_channel(
        payload.channel_id
    )

    if channel is None:
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

    print(
        f"🚨 BULK DELETE: "
        f"{len(payload.message_ids)} messages"
    )

    deleter = (
        "Unknown — no matching Audit Log entry"
    )

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

            if age < 0 or age > 30:
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

            if (
                getattr(
                    audit_channel,
                    "id",
                    None
                ) == LOG_CHANNEL_ID
            ):

                deleter = (
                    f"{entry.user} "
                    f"({entry.user.id})"
                )

                break

    except discord.Forbidden:

        deleter = (
            "Unknown — bot cannot read Audit Log"
        )

    except Exception as e:

        print(
            f"⚠️ Bulk Audit Log error: {e}"
        )

    recovered = []

    for message_id in payload.message_ids:

        saved = get_saved_message(
            message_id
        )

        if saved:

            content = (
                saved["embeds"]
                or saved["content"]
                or "(empty log)"
            )

            if len(content) > 500:
                content = content[:500] + "..."

            recovered.append(
                f"`{message_id}`\n{content}"
            )

            delete_saved_message(
                message_id
            )

    if not recovered:

        recovered_text = (
            "Original logs unavailable."
        )

    else:

        recovered_text = "\n\n".join(
            recovered[:10]
        )

        if len(recovered) > 10:

            recovered_text += (
                f"\n\n...and "
                f"{len(recovered) - 10} more."
            )

    if len(recovered_text) > 3500:

        recovered_text = (
            recovered_text[:3500]
            + "\n...[truncated]"
        )

    alert = (
        "🚨 **BULK LOG TAMPERING DETECTED**\n\n"

        f"**Server:** {guild.name}\n"
        f"**Channel:** #{channel.name}\n"
        f"**Messages deleted:** "
        f"{len(payload.message_ids)}\n\n"

        f"**WHO DELETED THEM:**\n"
        f"⚠️ {deleter}\n\n"

        f"**WHAT WAS DELETED:**\n"
        f"{recovered_text}"
    )

    await send_alert(alert)


# ============================================================
# PROTECTED LOG CHANNEL DELETED
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
        "Unknown — could not verify"
    )

    try:

        await asyncio.sleep(1)

        now = discord.utils.utcnow()

        async for entry in channel.guild.audit_logs(
            limit=25,
            action=discord.AuditLogAction.channel_delete
        ):

            age = (
                now - entry.created_at
            ).total_seconds()

            if age < 0 or age > 30:
                continue

            if (
                getattr(
                    entry.target,
                    "id",
                    None
                ) == channel.id
            ):

                deleter = (
                    f"{entry.user} "
                    f"({entry.user.id})"
                )

                break

    except discord.Forbidden:

        deleter = (
            "Unknown — bot cannot read Audit Log"
        )

    except Exception as e:

        print(
            f"⚠️ Channel deletion error: {e}"
        )

    await send_alert(
        "🚨 **PROTECTED LOG CHANNEL DELETED**\n\n"
        f"**Server:** {channel.guild.name}\n"
        f"**Channel:** #{channel.name}\n"
        f"**Channel ID:** `{channel.id}`\n\n"
        f"**WHO DELETED IT:**\n"
        f"⚠️ {deleter}"
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

    print("🚀 Starting Log Guard...")

    bot.run(TOKEN)



