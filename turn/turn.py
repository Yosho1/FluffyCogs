import asyncio
import collections
import contextlib
import functools
import typing

import discord

from redbot.core import commands, checks, Config

from .namedlist import NamedList


Cog = getattr(commands, "Cog", object)


class Game(NamedList):
    __slots__ = "queue", "destination", "source", "time", "paused", "task"


def standstr(argument):
    return "_".join(argument.lower().split())


def nonnegative_int(argument):
    i = int(argument)
    if i < 0:
        raise commands.BadArgument("Argument must not be negative.")
    return i


def is_all(argument):
    if argument.lower() == "all":
        return True
    raise commands.BadArgument()


def skipcheck():
    async def predicate(ctx):
        cog = ctx.bot.get_cog("Turn")
        if not cog:
            return False
        queue = cog.get(ctx).queue
        if queue and queue[0] == ctx.author:
            return True
        return await checks.is_mod_or_superior(ctx)

    return commands.check(predicate)


def gamecheck(is_running=True):
    def predicate(ctx):
        cog = ctx.bot.get_cog("Turn")
        if not cog:
            return False
        return is_running == bool(cog.get(ctx).task)

    return commands.check(predicate)


class Turn(Cog):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.games = {}
        self.config = Config.get_conf(self, identifier=2113674295, force_registration=True)
        self.config.register_guild(games={})

    def default(self, ctx):
        return self.games.setdefault(ctx.guild, Game(collections.deque()))

    def get(self, ctx):
        return self.games.get(ctx.guild, Game(collections.deque()))

    def serialize(self, ctx):
        try:
            g = list(self.games[ctx.guild])[:4]
            g[0] = list(map(lambda m: m.id, g[0]))
            g[1], g[2] = g[1].id if g[1] else None, g[2].id if g[2] else None
            return g
        except KeyError:
            return None

    @commands.group(aliases=["turns"])
    @commands.guild_only()
    async def turn(self, ctx):
        """Manage turns in a channel."""
        pass

    @turn.command()
    @checks.mod()
    @commands.guild_only()
    async def add(self, ctx, *members: discord.Member):
        """Add members to the queue."""
        self.default(ctx).queue.extend(members)
        await ctx.send("Queue: " + ", ".join(map(str, self.get(ctx).queue)))

    @turn.command()
    @checks.mod()
    @commands.guild_only()
    @gamecheck(False)
    async def load(self, ctx, *, name: standstr):
        """Load a previously saved turn set."""
        l = await self.config.guild(ctx.guild).get_raw("games", name)
        l[0] = collections.deque(map(ctx.guild.get_member, l[0]))
        gc = ctx.guild.get_channel
        l[1], l[2] = gc(l[1]), gc(l[2])
        g = Game(*l)
        self.games[ctx.guild] = g
        await ctx.send("Queue: " + ", ".join(map(str, self.get(ctx).queue)))

    @turn.command()
    @checks.mod()
    @commands.guild_only()
    @skipcheck()
    async def pause(self, ctx):
        """Pauses the timer.

        The bot will wait indefinitely for the current member, rather than skipping when time is up."""
        self.games[ctx.guild].paused = True
        await ctx.tick()

    @turn.command()
    @checks.mod()
    @commands.guild_only()
    async def remove(self, ctx, all: typing.Optional[is_all] = False, *, member: discord.Member):
        """Remove a member from the queue.

        If `remove all` is used, the member is removed completely.
        Otherwise, only the member's next turn is removed."""
        with contextlib.suppress(ValueError):
            if all:
                while True:
                    self.default(ctx).queue.remove(member)
            else:
                self.default(ctx).queue.remove(member)
        await ctx.send("Queue: " + ", ".join(map(str, self.get(ctx).queue)))

    @turn.command()
    @checks.mod()
    @commands.guild_only()
    @gamecheck(False)
    async def save(self, ctx, *, name: standstr):
        """Save the current turn settings to disk."""
        await self.config.guild(ctx.guild).set_raw("game", name, value=self.serialize(ctx))
        await ctx.tick()

    @turn.group(name="set")
    @checks.mod()
    @commands.guild_only()
    async def turn_set(self, ctx):
        """Configure turn settings."""
        pass

    @turn_set.command()
    @checks.mod()
    @commands.guild_only()
    async def destination(self, ctx, *, channel: discord.TextChannel = None):
        """Change where the bot announces turns."""
        channel = channel or ctx.channel
        g = self.default(ctx)
        g.destination = channel
        g.source = g.source or channel
        await ctx.tick()

    @turn_set.command()
    @checks.mod()
    @commands.guild_only()
    async def source(self, ctx, *, channel: discord.TextChannel = None):
        """Change where the bot will look for messages."""
        channel = channel or ctx.channel
        g = self.default(ctx)
        g.source = channel
        g.destination = g.destination or channel
        await ctx.tick()

    @turn_set.command()
    @checks.mod()
    @commands.guild_only()
    async def time(self, ctx, *, time: nonnegative_int):
        """Change how long the bot will wait for a message.

        The bot will reset the timer on seeing a typing indicator.
        A time of 0 will cause the bot to wait indefinitely."""
        self.default(ctx).time = time
        await ctx.tick()

    @turn.command(aliases=["next"])
    @commands.guild_only()
    @gamecheck()
    @skipcheck()
    async def skip(self, ctx, *, amount: int = 1):
        """Skip the specified amount of people.

        Specify a negative number to rewind the queue."""
        self.games[ctx.guild].queue.rotate(-amount)
        self.games[ctx.guild].task.cancel()
        await ctx.send("Queue: " + ", ".join(map(str, self.get(ctx).queue)))

    @turn.command()
    @checks.mod()
    @commands.guild_only()
    @gamecheck(False)
    async def start(self, ctx):
        """Begin detecting and announcing the turn order."""
        g = self.games[ctx.guild]
        if not g.queue:
            return await ctx.send("Not yet setup.")
        g.source = g.source or ctx.channel
        g.destination = g.destination or ctx.channel
        g.time = 600 if g.time is None else g.time
        g.paused = False
        g.task = ctx.bot.loop.create_task(self.task(ctx.guild))
        await ctx.tick()

    @turn.command()
    @checks.mod()
    @commands.guild_only()
    @gamecheck()
    async def stop(self, ctx):
        """Stop detecting and announcing the turn order."""
        self.games.pop(ctx.guild).task.cancel()
        await ctx.tick()

    def __unload(self):
        for k in self.games.copy():
            v = self.games.pop(k)
            t = v.task
            if t:
                t.cancel()

    __del__ = __unload

    async def task(self, guild: discord.Guild):
        # force a KeyError as soon as possible
        g = functools.partial(self.games.__getitem__, guild)
        # block the bot until waiting
        t = self.bot.loop.create_task

        m = g().queue[0]
        pings = 1

        def typing_check(channel, author, _):
            return channel == g().source and author == g().queue[0]

        def msg_check(msg):
            return msg.channel == g().source and msg.author == g().queue[0]

        with contextlib.suppress(KeyError):
            while self is self.bot.get_cog(self.__class__.__name__):
                with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                    if m != g().queue[0]:
                        m = g().queue[0]
                        pings = 1
                    if not g().paused:
                        t(g().destination.send(f"{m.mention}, you're up. Ping #{pings}."))
                    try:
                        if g().paused:
                            timeout = None
                        elif g().time:
                            timeout = g().time // 5
                        else:
                            timeout = 300
                        await self.bot.wait_for("typing", check=typing_check, timeout=timeout)
                    except asyncio.TimeoutError:
                        if g().paused or m != g().queue[0]:
                            continue
                        if not g().time or pings < 5:
                            pings += 1
                            continue
                        t(g().destination.send(f"No reply from {m.display_name}. Skipping..."))
                    else:
                        await self.bot.wait_for("message", check=msg_check, timeout=g().time)
                    g().paused = False
                    g().queue.rotate(-1)