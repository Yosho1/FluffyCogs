import aiohttp
import discord
import inflection
import itertools
import random
from typing import Union

from redbot.core import commands, checks, Config
from redbot.core.bot import Red
from redbot.core.i18n import get_locale
from redbot.core.utils.chat_formatting import italics

from .helpers import *


Cog = getattr(commands, "Cog", object)
listener = getattr(Cog, "listener", lambda: lambda x: x)


get_shared_api_tokens = getattr(Red, "get_shared_api_tokens", None)
if not get_shared_api_tokens:
    # pylint: disable=function-redefined
    async def get_shared_api_tokens(bot: Red, service_name: str):
        return await bot.db.api_tokens.get_raw(service_name, default={})


set_shared_api_tokens = getattr(Red, "set_shared_api_tokens", None)
if not set_shared_api_tokens:
    # pylint: disable=function-redefined
    async def set_shared_api_tokens(bot: Red, service_name: str, **tokens: str):
        async with bot.db.api_tokens.get_attr(service_name)() as method_abuse:
            method_abuse.update(**tokens)


class Act(Cog):

    __author__ = "Zephyrkul"

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=2_113_674_295, force_registration=True)
        self.config.register_global(custom={}, tenorkey=None)
        self.config.register_guild(custom={})

    async def initialize(self, bot):
        # temporary backwards compatibility
        key = await self.config.tenorkey()
        if not key:
            return
        await set_shared_api_tokens(bot, "tenor", api_key=key)
        await self.config.tenorkey.clear()

    @commands.command(hidden=True)
    async def act(self, ctx, *, target: Union[discord.Member, str] = None):
        """
        Acts on the specified user.
        """
        if not target or isinstance(target, str):
            return  # no help text

        try:
            if not ctx.guild:
                raise KeyError()
            message = await self.config.guild(ctx.guild).get_raw("custom", ctx.invoked_with)
        except KeyError:
            try:
                message = await self.config.get_raw("custom", ctx.invoked_with)
            except KeyError:
                message = NotImplemented

        if message is None:  # ignored command
            return
        elif message is NotImplemented:  # default
            # humanize action text
            action = inflection.humanize(ctx.invoked_with).split()
            iverb = -1

            for cycle in range(2):
                if iverb > -1:
                    break
                for i, act in enumerate(action):
                    act = act.lower()
                    if (
                        act in NOLY_ADV
                        or act in CONJ
                        or (act.endswith("ly") and act not in LY_VERBS)
                        or (not cycle and act in SOFT_VERBS)
                    ):
                        continue
                    action[i] = inflection.pluralize(action[i])
                    iverb = max(iverb, i)

            if iverb < 0:
                return
            action.insert(iverb + 1, target.mention)
            message = italics(" ".join(action))
        else:
            message = message.format(target, user=target)

        # add reaction gif
        if not ctx.channel.permissions_for(ctx.me).embed_links:
            return await ctx.send(message)
        key = (await get_shared_api_tokens(ctx.bot, "tenor")).get("api_key")
        if not key:
            return await ctx.send(message)
        async with aiohttp.request(
            "GET",
            "https://api.tenor.com/v1/search",
            params={
                "q": ctx.invoked_with,
                "key": key,
                "anon_id": str(ctx.author.id ^ ctx.me.id),
                "media_filter": "minimal",
                "contentfilter": "low",
                "ar_range": "wide",
                "locale": get_locale(),
            },
        ) as response:
            if response.status >= 400:
                json: dict = {}
            else:
                json = await response.json()
        if not json.get("results"):
            return await ctx.send(message)
        message = f"{message}\n\n{random.choice(json['results'])['itemurl']}"
        await ctx.send(message)

    @commands.group()
    @checks.is_owner()
    async def actset(self, ctx):
        """
        Configure various settings for the act cog.
        """
        pass

    @actset.group(aliases=["custom"], invoke_without_command=True)
    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def customize(self, ctx, command: str.lower, *, response: str = None):
        """
        Customize the response to an action.

        You can use {0} or {user} to dynamically replace with the specified target of the action.
        Formats like {0.name} or {0.mention} can also be used.
        """
        if not response:
            await self.config.guild(ctx.guild).clear_raw("custom", command)
        else:
            await self.config.guild(ctx.guild).set_raw("custom", command, value=response)
        await ctx.tick()

    @customize.command(name="global")
    @checks.is_owner()
    async def customize_global(self, ctx, command: str.lower, *, response: str = None):
        """
        Globally customize the response to an action.

        You can use {0} or {user} to dynamically replace with the specified target of the action.
        Formats like {0.name} or {0.mention} can also be used.
        """
        if not response:
            await self.config.clear_raw("custom", command)
        else:
            await self.config.set_raw("custom", command, value=response)
        await ctx.tick()

    @actset.group(invoke_without_command=True)
    @checks.admin_or_permissions(manage_guild=True)
    @commands.guild_only()
    async def ignore(self, ctx, command: str.lower):
        """
        Ignore or unignore the specified action.

        The bot will no longer respond to these actions.
        """
        try:
            custom = await self.config.guild(ctx.guild).get_raw("custom", command)
        except KeyError:
            custom = NotImplemented
        if custom is None:
            await self.config.guild(ctx.guild).clear_raw("custom", command)
            await ctx.send("I will no longer ignore the {command} action".format(command=command))
        else:
            await self.config.guild(ctx.guild).set_raw("custom", command, value=None)
            await ctx.send("I will now ignore the {command} action".format(command=command))

    @ignore.command(name="global")
    @checks.is_owner()
    async def ignore_global(self, ctx, command: str.lower):
        """
        Globally ignore or unignore the specified action.

        The bot will no longer respond to these actions.
        """
        try:
            await self.config.get_raw("custom", command)
        except KeyError:
            await self.config.set_raw("custom", command, value=None)
        else:
            await self.config.clear_raw("custom", command)
        await ctx.tick()

    @actset.command()
    @checks.is_owner()
    async def tenorkey(self, ctx):
        """
        Sets a Tenor GIF API key to enable reaction gifs with act commands.

        You can obtain a key from here: https://tenor.com/developer/dashboard
        """
        instructions = [
            "Go to the Tenor developer dashboard: https://tenor.com/developer/dashboard",
            "Log in or sign up if you haven't already.",
            "Click `+ Create new app` and fill out the form.",
            "Copy the key from the app you just created.",
            "Give the key to Red with this command:\n"
            f"`{ctx.prefix}set api tenor api_key your_api_key`\n"
            "Replace `your_api_key` with the key you just got.\n"
            "Everything else should be the same.",
        ]
        instructions = [f"**{i}.** {v}" for i, v in enumerate(instructions, 1)]
        await ctx.maybe_send_embed("\n".join(instructions))

    @listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        ctx = await self.bot.get_context(message)
        if ctx.prefix is None or not ctx.invoked_with.replace("_", "").isalpha():
            return

        if ctx.valid and ctx.command.enabled:
            try:
                if await ctx.command.can_run(ctx):
                    return
            except commands.errors.CheckFailure:
                return

        ctx.command = self.act
        await self.bot.invoke(ctx)
