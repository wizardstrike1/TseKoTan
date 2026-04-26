import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Optional, Set

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

OWNER_ID = 528953104939483186
STORAGE_PATH = os.path.join(os.path.dirname(__file__), "storage.json")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class Storage:
    user_whitelist: Set[int]
    ping_role_whitelist: Set[int]

    @staticmethod
    def load(path: str) -> "Storage":
        if not os.path.exists(path):
            s = Storage(user_whitelist={OWNER_ID}, ping_role_whitelist=set())
            s.save(path)
            return s
        with open(path, "r", encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
        uw = {int(x) for x in data.get("user_whitelist", [])}
        pr = {int(x) for x in data.get("ping_role_whitelist", [])}
        uw.add(OWNER_ID)
        return Storage(user_whitelist=uw, ping_role_whitelist=pr)

    def save(self, path: str) -> None:
        tmp = {
            "user_whitelist": sorted(self.user_whitelist),
            "ping_role_whitelist": sorted(self.ping_role_whitelist),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(tmp, f, indent=2)


class WhitelistBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True  # needed to enumerate guild members
        intents.presences = True  # only used when DM_ONLY_ONLINE=true
        super().__init__(command_prefix="!", intents=intents)

        self.storage = Storage.load(STORAGE_PATH)
        self.dm_only_online = _env_bool("DM_ONLY_ONLINE", False)
        self.dm_delay_seconds = _env_float("DM_DELAY_SECONDS", 1.2)

    async def setup_hook(self) -> None:
        await self.tree.sync()


bot = WhitelistBot()


def _is_whitelisted_user(user_id: int) -> bool:
    return user_id in bot.storage.user_whitelist


async def _ensure_authorized(interaction: discord.Interaction) -> bool:
    if interaction.user is None:
        return False
    if _is_whitelisted_user(interaction.user.id):
        return True
    try:
        await interaction.response.send_message(
            "You are not authorized to use this command.", ephemeral=True
        )
    except discord.InteractionResponded:
        await interaction.followup.send(
            "You are not authorized to use this command.", ephemeral=True
        )
    return False


async def _safe_dm(
    member: discord.Member,
    content: str,
    *,
    dm_delay_seconds: float,
) -> tuple[bool, Optional[str]]:
    """
    Returns (ok, error_reason_if_any).
    discord.py already rate-limits per route, but we also add a gentle delay
    between sends to reduce the chance of hitting global limits.
    """
    try:
        await member.send(content)
        await asyncio.sleep(dm_delay_seconds)
        return True, None
    except discord.Forbidden:
        await asyncio.sleep(dm_delay_seconds)
        return False, "forbidden"
    except discord.HTTPException as e:
        # 429s are typically handled internally, but if one leaks through, back off.
        retry_after = getattr(e, "retry_after", None)
        if retry_after is not None:
            await asyncio.sleep(float(retry_after) + dm_delay_seconds)
            return False, f"http_{e.status}_retry_after"
        await asyncio.sleep(dm_delay_seconds)
        return False, f"http_{getattr(e, 'status', 'error')}"


@bot.tree.command(name="whitelist", description="Add a user to the command whitelist.")
@app_commands.describe(user="User to whitelist")
async def whitelist_cmd(interaction: discord.Interaction, user: discord.User) -> None:
    if not await _ensure_authorized(interaction):
        return

    bot.storage.user_whitelist.add(int(user.id))
    bot.storage.user_whitelist.add(OWNER_ID)
    bot.storage.save(STORAGE_PATH)

    await interaction.response.send_message(
        f"Whitelisted: `{user.id}`", ephemeral=True
    )


@bot.tree.command(
    name="addping",
    description="Add a server role to the ping-role whitelist (used by /rallydm).",
)
@app_commands.describe(role="Role to add")
async def addping_cmd(interaction: discord.Interaction, role: discord.Role) -> None:
    if not await _ensure_authorized(interaction):
        return

    bot.storage.ping_role_whitelist.add(int(role.id))
    bot.storage.save(STORAGE_PATH)

    await interaction.response.send_message(
        f"Added ping role whitelist: `{role.id}`", ephemeral=True
    )


def _member_has_any_whitelisted_role(member: discord.Member) -> bool:
    if not bot.storage.ping_role_whitelist:
        return False
    role_ids = {r.id for r in member.roles}
    return any(rid in role_ids for rid in bot.storage.ping_role_whitelist)


def _is_online(member: discord.Member) -> bool:
    # If Presence Intent is off, member.status may be "offline"/unknown-ish.
    # We treat "offline" explicitly as not online; everything else counts as online.
    try:
        return member.status is not discord.Status.offline
    except Exception:
        return True


@bot.tree.command(
    name="rallydm",
    description="DM a message to members who have any role in the ping-role whitelist.",
)
@app_commands.describe(message="Message to DM")
async def rallydm_cmd(interaction: discord.Interaction, message: str) -> None:
    if not await _ensure_authorized(interaction):
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "This command must be used in a server.", ephemeral=True
        )
        return

    if not bot.storage.ping_role_whitelist:
        await interaction.response.send_message(
            "No ping roles are whitelisted yet. Use `/addping` first.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    guild = interaction.guild

    # Ensure member cache is populated enough to iterate.
    # This can take a bit on large servers; we handle with defer above.
    try:
        members = [m async for m in guild.fetch_members(limit=None)]
    except discord.Forbidden:
        await interaction.followup.send(
            "I don't have permission to list members. Enable Server Members Intent and ensure I can view members.",
            ephemeral=True,
        )
        return

    targets: list[discord.Member] = []
    for m in members:
        if m.bot:
            continue
        if not _member_has_any_whitelisted_role(m):
            continue
        if bot.dm_only_online and not _is_online(m):
            continue
        targets.append(m)

    sent = 0
    failed = 0
    forbidden = 0

    # Gentle concurrency (avoid spiking)
    sem = asyncio.Semaphore(3)

    async def worker(member: discord.Member) -> None:
        nonlocal sent, failed, forbidden
        async with sem:
            ok, reason = await _safe_dm(
                member, message, dm_delay_seconds=bot.dm_delay_seconds
            )
            if ok:
                sent += 1
            else:
                failed += 1
                if reason == "forbidden":
                    forbidden += 1

    for chunk_start in range(0, len(targets), 30):
        chunk = targets[chunk_start : chunk_start + 30]
        await asyncio.gather(*(worker(m) for m in chunk))

    await interaction.followup.send(
        f"Done. Targeted `{len(targets)}` member(s). Sent `{sent}`. Failed `{failed}` (forbidden `{forbidden}`).",
        ephemeral=True,
    )


@bot.event
async def on_ready() -> None:
    print(f"Logged in as {bot.user} (id={bot.user.id})")
    print(f"Whitelisted users: {sorted(bot.storage.user_whitelist)}")
    print(f"Whitelisted ping roles: {sorted(bot.storage.ping_role_whitelist)}")


def main() -> None:
    load_dotenv()
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "Missing DISCORD_BOT_TOKEN. Copy config.example.env to .env and fill DISCORD_BOT_TOKEN="
        )
    bot.run(token)


if __name__ == "__main__":
    main()

