import logging
import time
import discord
from discord.ext import tasks
from clients.uscis import UscisClient
from db.connection import get_db
import config

logger = logging.getLogger(__name__)

bot_instance = None
uscis_client = UscisClient()

POLL_INTERVAL_HOURS = 2


async def _init_table():
    """Create the uscis_case_status table if it doesn't exist."""
    db = await get_db()
    await db.execute("""
        CREATE TABLE IF NOT EXISTS uscis_case_status (
            receipt_number TEXT PRIMARY KEY,
            last_status TEXT,
            last_detail TEXT,
            last_checked REAL
        )
    """)
    await db.commit()


async def _get_last_status(receipt_number: str) -> str | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT last_status FROM uscis_case_status WHERE receipt_number = ?",
        (receipt_number,)
    )
    row = await cursor.fetchone()
    return row["last_status"] if row else None


async def _save_status(receipt_number: str, status: str, detail: str):
    db = await get_db()
    await db.execute("""
        INSERT INTO uscis_case_status (receipt_number, last_status, last_detail, last_checked)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(receipt_number) DO UPDATE SET
            last_status = excluded.last_status,
            last_detail = excluded.last_detail,
            last_checked = excluded.last_checked
    """, (receipt_number, status, detail, time.time()))
    await db.commit()


async def _dm_user(title: str, message: str, color: int = 0x2ecc71):
    """Send a DM to the configured user."""
    user_id = getattr(config, "USCIS_DM_USER_ID", None)
    if not user_id or not bot_instance:
        return

    try:
        user = await bot_instance.fetch_user(int(user_id))
        embed = discord.Embed(title=title, description=message, color=color)
        await user.send(embed=embed)
        logger.info(f"[USCIS] DM sent to user {user_id}")
    except Exception as e:
        logger.error(f"[USCIS] Failed to DM user {user_id}: {e}")


@tasks.loop(hours=POLL_INTERVAL_HOURS)
async def poll_uscis_cases():
    """Poll USCIS for case status updates and DM on changes."""
    if bot_instance is None:
        return

    receipt_numbers = getattr(config, "USCIS_RECEIPT_NUMBERS", [])
    if not receipt_numbers:
        logger.debug("[USCIS] No receipt numbers configured, skipping poll.")
        return

    for receipt in receipt_numbers:
        try:
            data = await uscis_client.get_case_status(receipt)

            # MyCasesHub response — may be nested under "data"
            inner = data.get("data", data)
            current_status = inner.get("caseStatus", inner.get("status", "Unknown"))
            form_type = inner.get("formType", "")
            detail = inner.get("description", inner.get("statusText", ""))
            if form_type:
                detail = f"**Form:** {form_type}\n{detail}"

            previous_status = await _get_last_status(receipt)
            await _save_status(receipt, current_status, detail)

            if previous_status is None:
                # First check — notify with initial status
                await _dm_user(
                    f"USCIS Tracker Started: {receipt}",
                    f"**Status:** {current_status}\n\n{detail}",
                    color=0x3498db
                )
                logger.info(f"[USCIS] Initial status for {receipt}: {current_status}")
            elif current_status != previous_status:
                await _dm_user(
                    f"USCIS Status Changed: {receipt}",
                    f"**Previous:** {previous_status}\n**New:** {current_status}\n\n{detail}",
                    color=0xe74c3c
                )
                logger.info(f"[USCIS] Status changed for {receipt}: {previous_status} -> {current_status}")
            else:
                logger.debug(f"[USCIS] No change for {receipt}: {current_status}")

        except Exception as e:
            logger.error(f"[USCIS] Error checking {receipt}: {e}")


def setup_uscis_tracker(bot):
    global bot_instance
    bot_instance = bot

    # Initialize table before starting the loop
    async def init_and_start():
        await _init_table()
        poll_uscis_cases.start()

    bot.loop.create_task(init_and_start())
    logger.info(f"[USCIS] Tracker started (polling every {POLL_INTERVAL_HOURS}h)")
