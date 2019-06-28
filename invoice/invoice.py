import asyncio
import discord
import typing

from copy import copy
from redbot.core import checks, commands, Config

listener = getattr(commands.Cog, "listener", lambda name=None: (lambda f: f))


class InVoice(commands.Cog):
    def __init__(self):
        self.config = Config.get_conf(self, identifier=2113674295, force_registration=True)
        self.config.register_guild(
            role=None, dynamic=False, mute=False, deaf=False, self_deaf=False
        )
        self.config.register_channel(role=None, channel=None)

    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def invoice(self, ctx):
        """
        Configure or view settings for automated voice-based permissions.
        """
        if ctx.invoked_subcommand:
            return
        color = await ctx.embed_colour()

        embed = discord.Embed(title=f"Guild {ctx.guild} settings:\n", color=color)
        g_settings = await self.config.guild(ctx.guild).all()
        for key, value in g_settings.items():
            if value is not None and key == "role":
                value = f"<@&{value}>"
            key = key.replace("_", " ").title()
            embed.add_field(name=key, value=value)
        await ctx.send(embed=embed)

        vc = ctx.author.voice.channel if ctx.author.voice else None
        if vc:
            embed = discord.Embed(title=f"Channel {vc} settings:\n", color=color)
            c_settings = await self.config.channel(vc).all()
            for key, value in c_settings.items():
                if value is not None:
                    if key == "role":
                        value = f"<@&{value}>"
                    if key == "channel":
                        value = f"<#{value}>"
                key = key.replace("_", " ").title()
                embed.add_field(name=key, value=value)
            await ctx.send(embed=embed)

    @invoice.command()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def dynamic(self, ctx, *, true_or_false: bool = None):
        """
        Toggle whether to dynamically create a role and channel for new voice channels when they're created.
        """
        if true_or_false is None:
            true_or_false = not await self.config.guild(ctx.guild).dynamic()
        await self.config.guild(ctx.guild).dynamic.set(true_or_false)
        await ctx.tick()

    # TODO: mute, deaf, selfdeaf

    @invoice.command(aliases=["server"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def guild(self, ctx, *, role: discord.Role = None):
        """
        Set a guild-wide role for users who are in any non-AFK voice channel.
        """
        if not role:
            await self.config.guild(ctx.guild).role.clear()
            await ctx.send("Role cleared.")
        else:
            await self.config.guild(ctx.guild).role.set(role.id)
            await ctx.send("Role set to {role}.".format(role=role))

    @invoice.command()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def link(
        self,
        ctx,
        vc: discord.VoiceChannel,
        *,
        role_or_channel: typing.Union[discord.Role, discord.TextChannel, None] = None,
    ):
        """
        Links a role or text channel to a voice channel.

        When a member joins or leaves the channel, the role is applied accordingly.

        As well, if the related settings are enabled:
            When a member becomes deafened or undeafened, the role is applied accordingly.
            When a member becomes server muted or unmuted, the channel permissions are updated accordingly.

        If a role or channel is not set, the bot will update the other instead.
        """
        if not role_or_channel:
            await self.config.channel(vc).clear()
            await ctx.send("Link(s) for {vc} cleared.".format(vc=vc))
        elif isinstance(role_or_channel, discord.Role):
            await self.config.channel(vc).role.set(role_or_channel.id)
            await ctx.send("Role for {vc} set to {role}.".format(vc=vc, role=role_or_channel))
        else:
            if vc == ctx.guild.afk_channel:
                return await ctx.send("Text channels cannot be linked to the guild's AFK channel.")
            await self.config.channel(vc).channel.set(role_or_channel.id)
            await ctx.send(
                "Text channel for {vc} set to {channel}".format(vc=vc, channel=role_or_channel)
            )

    @listener()
    async def on_guild_channel_create(self, vc):
        if not isinstance(vc, discord.VoiceChannel):
            return
        guild = vc.guild
        if not await self.config.guild(guild).dynamic():
            return
        name = "🔊 " + vc.name
        role = await guild.create_role(name=name, reason="Dynamic role for {vc}".format(vc=vc))
        await self.config.channel(vc).role.set(role.id)
        if vc.category:
            def_over = vc.category.overwrites_for(guild.default_role)
            def_over.read_messages = False
        else:
            def_over = discord.PermissionOverwrite(read_messages=False)
        role_over = discord.PermissionOverwrite(**dict(def_over))
        role_over.update(read_messages=True, send_messages=True)
        text = await guild.create_text_channel(
            name=name,
            overwrites={guild.default_role: def_over, role: role_over, guild.me: role_over},
            category=vc.category,
            reason="Dynamic channel for {vc}".format(vc=vc),
        )
        await self.config.channel(vc).channel.set(text.id)

    @listener()
    async def on_guild_channel_delete(self, vc):
        if not isinstance(vc, discord.VoiceChannel):
            return
        guild = vc.guild
        async with self.config.channel(vc).all() as conf:
            settings = conf.copy()
            conf.clear()
        if not await self.config.guild(guild).dynamic():
            return
        role = guild.get_role(settings["role"])
        if role:
            await role.delete(reason="Dynamic role for {vc}".format(vc=vc))
        channel = guild.get_channel(settings["channel"])
        if channel:
            await channel.delete(reason="Dynamic channel for {vc}".format(vc=vc))

    @listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        if not before.channel and not after.channel:
            return  # I doubt this could happen, but just in case
        if before.channel != after.channel:
            await self.channel_update(member, before, after)
        elif before.mute != after.mute:
            await self.mute_update(member, before, after)
        elif before.deaf != after.deaf:
            await self.deaf_update(member, before, after)
        elif before.self_deaf != after.self_deaf:
            await self.self_deaf_update(member, before, after)

    async def channel_update(self, m, b, a):
        guild_role = m.guild.get_role(await self.config.guild(m.guild).role())
        if b.channel:
            reason = "Left channel {vc}".format(vc=b.channel)
            to_remove = []
            role = m.guild.get_role(await self.config.channel(b.channel).role())
            if role and role in m.roles:
                to_remove.append(role)
            if (not a.channel or a.afk) and guild_role and guild_role in m.roles:
                to_remove.append(guild_role)
            if to_remove:
                await m.remove_roles(*to_remove, reason=reason)
            tc = m.guild.get_channel(await self.config.channel(b.channel).channel())
            if tc and m in tc.overwrites:
                await tc.set_permissions(target=m, overwrite=None, reason=reason)
        if a.channel:
            reason = "Joined channel {vc}".format(vc=a.channel)
            to_add = []
            role = m.guild.get_role(await self.config.channel(a.channel).role())
            if role and role not in m.roles:
                to_add.append(role)
            if guild_role and not a.afk and guild_role not in m.roles:
                to_add.append(guild_role)
            if to_add:
                await m.add_roles(*to_add, reason=reason)
            tc = m.guild.get_channel(await self.config.channel(a.channel).channel())
            if tc and m in tc.overwrites:
                await tc.set_permissions(target=m, overwrite=None, reason=reason)

    async def mute_update(self, m, b, a):
        # TODO: UNMUTE
        if not await self.config.guild(m.guild).mute():
            return
        tc = m.guild.get_channel(await self.config.channel(a.channel).channel())
        if tc:
            overs = tc.overwrites_for(m)
            overs.send_messages = False
            await tc.set_permissions(target=m, overwrite=overs, reason="Server muted")
        else:
            roles = (
                await self.config.guild(m.guild).role(),
                await self.config.channel(a.channel).role(),
            )
            roles = map(m.guild.get_role, roles)
            roles = tuple(filter(bool, roles))
            if roles:
                await m.remove_roles(*roles, reason="Server muted")

    async def deaf_update(self, m, b, a):
        # TODO: UNDEAF
        if not await self.config.guild(m.guild).deaf():
            return
        await self._deaf_update(m, b, a, reason="Server deafened")

    async def self_deaf_update(self, m, b, a):
        # TODO: UNDEAF
        if not await self.config.guild(m.guild).self_deaf():
            return
        await self._deaf_update(m, b, a, reason="Self deafened")

    async def _deaf_update(self, m, b, a, *, reason):
        role = m.guild.get_role(await self.config.channel(a.channel).role())
        if role:
            guild_role = m.guild.get_role(await self.config.guild(m.guild).role())
            await m.remove_roles(guild_role, role, reason=reason)
        else:
            tc = m.guild.get_channel(await self.config.channel(a.channel).channel())
            overs = tc.overwrites_for(m)
            overs.read_messages = False
            await tc.set_permissions(target=m, overwrite=overs, reason=reason)

    async def _undeaf_update(self, m, b, a, *, reason):
        pass