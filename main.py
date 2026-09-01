@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):

    print(
        f"🗑️ DELETE EVENT: "
        f"{payload.message_id} in {payload.channel_id}"
    )

    # Only watch the configured log channel.
    if payload.channel_id != LOG_CHANNEL_ID:
        return

    # Get the actual channel first.
    channel = bot.get_channel(payload.channel_id)

    if channel is None:
        try:
            channel = await bot.fetch_channel(payload.channel_id)
        except Exception as e:
            print(f"❌ Could not fetch log channel: {e}")
            return

    # Get guild directly from the channel.
    guild = getattr(channel, "guild", None)

    if guild is None:
        print("❌ Could not determine guild from log channel.")
        return

    print(f"✅ Guild found: {guild.name} ({guild.id})")

    # Get cached information about the deleted message.
    cached = message_cache.pop(payload.message_id, None)

    if cached:
        author = cached["author"]
        content = cached["content"]

        author_id = author.id
        author_text = f"{author} ({author.id})"

        content_preview = (
            content[:500]
            if content
            else "(no text content)"
        )

        channel_name = channel.name

    else:
        author_id = 0
        author_text = "Unknown — message was not cached"
        content_preview = "(message content unavailable)"
        channel_name = getattr(channel, "name", "log channel")

    # Try to identify who deleted it.
    if author_id:
        deleter = await find_message_deleter(
            guild,
            author_id,
            LOG_CHANNEL_ID
        )
    else:
        deleter = (
            "Unknown — original message was not cached"
        )

    print(f"🚨 Sending deletion alert. Deleted by: {deleter}")

    # SEND DM
    await send_alert(
        "⚠️ **LOG MESSAGE DELETED**\n\n"
        f"**Server:** {guild.name}\n"
        f"**Channel:** #{channel_name}\n"
        f"**Message ID:** `{payload.message_id}`\n"
        f"**Original author:** {author_text}\n"
        f"**Deleted by:** {deleter}\n"
        f"**Content:** {content_preview}"
    )
