import time
import sys
import os
import re

# Import hacks to access utils and classes...
curdir = os.path.dirname(__file__)
sys.path += [curdir, os.path.dirname(curdir)]
import utils
from log import log
from classes import *

from ts6_common import TS6BaseProtocol

class InspIRCdProtocol(TS6BaseProtocol):
    def __init__(self, irc):
        super(InspIRCdProtocol, self).__init__(irc)
        # Set our case mapping (rfc1459 maps "\" and "|" together, for example).
        self.casemapping = 'rfc1459'

        # Raw commands sent from servers vary from protocol to protocol. Here, we map
        # non-standard names to our hook handlers, so command handlers' outputs
        # are called with the right hooks.
        self.hook_map = {'FJOIN': 'JOIN', 'RSQUIT': 'SQUIT', 'FMODE': 'MODE',
                    'FTOPIC': 'TOPIC', 'OPERTYPE': 'MODE', 'FHOST': 'CHGHOST',
                    'FIDENT': 'CHGIDENT', 'FNAME': 'CHGNAME', 'SVSTOPIC': 'TOPIC'}
        self.sidgen = utils.TS6SIDGenerator(self.irc)
        self.uidgen = {}

    def spawnClient(self, nick, ident='null', host='null', realhost=None, modes=set(),
            server=None, ip='0.0.0.0', realname=None, ts=None, opertype=None,
            manipulatable=False):
        """Spawns a client with nick <nick> on the given IRC connection.

        Note: No nick collision / valid nickname checks are done here; it is
        up to plugins to make sure they don't introduce anything invalid."""
        server = server or self.irc.sid
        if not utils.isInternalServer(self.irc, server):
            raise ValueError('Server %r is not a PyLink internal PseudoServer!' % server)
        # Create an UIDGenerator instance for every SID, so that each gets
        # distinct values.
        uid = self.uidgen.setdefault(server, utils.TS6UIDGenerator(server)).next_uid()
        ts = ts or int(time.time())
        realname = realname or self.irc.botdata['realname']
        realhost = realhost or host
        raw_modes = utils.joinModes(modes)
        u = self.irc.users[uid] = IrcUser(nick, ts, uid, ident=ident, host=host, realname=realname,
            realhost=realhost, ip=ip, manipulatable=manipulatable)
        utils.applyModes(self.irc, uid, modes)
        self.irc.servers[server].users.add(uid)
        self._send(server, "UID {uid} {ts} {nick} {realhost} {host} {ident} {ip}"
                        " {ts} {modes} + :{realname}".format(ts=ts, host=host,
                                                 nick=nick, ident=ident, uid=uid,
                                                 modes=raw_modes, ip=ip, realname=realname,
                                                 realhost=realhost))
        if ('o', None) in modes or ('+o', None) in modes:
            self._operUp(uid, opertype=opertype or 'IRC Operator')
        return u

    def joinClient(self, client, channel):
        """Joins a PyLink client to a channel."""
        # InspIRCd doesn't distinguish between burst joins and regular joins,
        # so what we're actually doing here is sending FJOIN from the server,
        # on behalf of the clients that are joining.
        channel = utils.toLower(self.irc, channel)
        server = utils.isInternalClient(self.irc, client)
        if not server:
            log.error('(%s) Error trying to join client %r to %r (no such pseudoclient exists)', self.irc.name, client, channel)
            raise LookupError('No such PyLink PseudoClient exists.')
        # Strip out list-modes, they shouldn't be ever sent in FJOIN.
        modes = [m for m in self.irc.channels[channel].modes if m[0] not in self.irc.cmodes['*A']]
        self._send(server, "FJOIN {channel} {ts} {modes} :,{uid}".format(
                ts=self.irc.channels[channel].ts, uid=client, channel=channel,
                modes=utils.joinModes(modes)))
        self.irc.channels[channel].users.add(client)
        self.irc.users[client].channels.add(channel)

    def sjoinServer(self, server, channel, users, ts=None):
        """Sends an SJOIN for a group of users to a channel.

        The sender should always be a Server ID (SID). TS is optional, and defaults
        to the one we've stored in the channel state if not given.
        <users> is a list of (prefix mode, UID) pairs:

        Example uses:
            sjoinServer('100', '#test', [('', '100AAABBC'), ('qo', 100AAABBB'), ('h', '100AAADDD')])
            sjoinServer(self.irc.sid, '#test', [('o', self.irc.pseudoclient.uid)])
        """
        channel = utils.toLower(self.irc, channel)
        server = server or self.irc.sid
        assert users, "sjoinServer: No users sent?"
        log.debug('(%s) sjoinServer: got %r for users', self.irc.name, users)
        if not server:
            raise LookupError('No such PyLink PseudoClient exists.')

        orig_ts = self.irc.channels[channel].ts
        ts = ts or orig_ts
        self.updateTS(channel, ts)

        log.debug("sending SJOIN to %s%s with ts %s (that's %r)", channel, self.irc.name, ts,
                  time.strftime("%c", time.localtime(ts)))
        # Strip out list-modes, they shouldn't ever be sent in FJOIN (protocol rules).
        modes = [m for m in self.irc.channels[channel].modes if m[0] not in self.irc.cmodes['*A']]
        uids = []
        changedmodes = []
        namelist = []
        # We take <users> as a list of (prefixmodes, uid) pairs.
        for userpair in users:
            assert len(userpair) == 2, "Incorrect format of userpair: %r" % userpair
            prefixes, user = userpair
            namelist.append(','.join(userpair))
            uids.append(user)
            for m in prefixes:
                changedmodes.append(('+%s' % m, user))
            try:
                self.irc.users[user].channels.add(channel)
            except KeyError:  # Not initialized yet?
                log.debug("(%s) sjoinServer: KeyError trying to add %r to %r's channel list?", self.irc.name, channel, user)
        if ts <= orig_ts:
            # Only save our prefix modes in the channel state if our TS is lower than or equal to theirs.
            utils.applyModes(self.irc, channel, changedmodes)
        namelist = ' '.join(namelist)
        self._send(server, "FJOIN {channel} {ts} {modes} :{users}".format(
                ts=ts, users=namelist, channel=channel,
                modes=utils.joinModes(modes)))
        self.irc.channels[channel].users.update(uids)

    def _operUp(self, target, opertype=None):
        """Opers a client up (internal function specific to InspIRCd).

        This should be called whenever user mode +o is set on anyone, because
        InspIRCd requires a special command (OPERTYPE) to be sent in order to
        recognize ANY non-burst oper ups.

        Plugins don't have to call this function themselves, but they can
        set the opertype attribute of an IrcUser object (in self.irc.users),
        and the change will be reflected here."""
        userobj = self.irc.users[target]
        try:
            otype = opertype or userobj.opertype or 'IRC Operator'
        except AttributeError:
            log.debug('(%s) opertype field for %s (%s) isn\'t filled yet!',
                      self.irc.name, target, userobj.nick)
            # whatever, this is non-standard anyways.
            otype = 'IRC Operator'
        assert otype, "Tried to send an empty OPERTYPE!"
        log.debug('(%s) Sending OPERTYPE from %s to oper them up.',
                  self.irc.name, target)
        userobj.opertype = otype
        self._send(target, 'OPERTYPE %s' % otype.replace(" ", "_"))

    def _sendModes(self, numeric, target, modes, ts=None):
        """Internal function to send mode changes from a PyLink client/server."""
        # -> :9PYAAAAAA FMODE #pylink 1433653951 +os 9PYAAAAAA
        # -> :9PYAAAAAA MODE 9PYAAAAAA -i+w
        log.debug('(%s) inspircd._sendModes: received %r for mode list', self.irc.name, modes)
        if ('+o', None) in modes and not utils.isChannel(target):
            # https://github.com/inspself.ircd/inspself.ircd/blob/master/src/modules/m_spanningtree/opertype.cpp#L26-L28
            # Servers need a special command to set umode +o on people.
            self._operUp(target)
        utils.applyModes(self.irc, target, modes)
        joinedmodes = utils.joinModes(modes)
        if utils.isChannel(target):
            ts = ts or self.irc.channels[utils.toLower(self.irc, target)].ts
            self._send(numeric, 'FMODE %s %s %s' % (target, ts, joinedmodes))
        else:
            self._send(numeric, 'MODE %s %s' % (target, joinedmodes))

    def modeClient(self, numeric, target, modes, ts=None):
        """
        Sends mode changes from a PyLink client. <modes> should be
        a list of (mode, arg) tuples, i.e. the format of utils.parseModes() output.
        """
        if not utils.isInternalClient(self.irc, numeric):
            raise LookupError('No such PyLink PseudoClient exists.')
        self._sendModes(numeric, target, modes, ts=ts)

    def modeServer(self, numeric, target, modes, ts=None):
        """
        Sends mode changes from a PyLink server. <list of modes> should be
        a list of (mode, arg) tuples, i.e. the format of utils.parseModes() output.
        """
        if not utils.isInternalServer(self.irc, numeric):
            raise LookupError('No such PyLink PseudoServer exists.')
        self._sendModes(numeric, target, modes, ts=ts)

    def _sendKill(self, numeric, target, reason):
        self._send(numeric, 'KILL %s :%s' % (target, reason))
        # We only need to call removeClient here if the target is one of our
        # clients, since any remote servers will send a QUIT from
        # their target if the command succeeds.
        if utils.isInternalClient(self.irc, target):
            self.removeClient(target)

    def killServer(self, numeric, target, reason):
        """Sends a kill from a PyLink server."""
        if not utils.isInternalServer(self.irc, numeric):
            raise LookupError('No such PyLink PseudoServer exists.')
        self._sendKill(numeric, target, reason)

    def killClient(self, numeric, target, reason):
        """Sends a kill from a PyLink client."""
        if not utils.isInternalClient(self.irc, numeric):
            raise LookupError('No such PyLink PseudoClient exists.')
        self._sendKill(numeric, target, reason)

    def topicServer(self, numeric, target, text):
        """Sends a topic change from a PyLink server. This is usually used on burst."""
        if not utils.isInternalServer(self.irc, numeric):
            raise LookupError('No such PyLink PseudoServer exists.')
        ts = int(time.time())
        servername = self.irc.servers[numeric].name
        self._send(numeric, 'FTOPIC %s %s %s :%s' % (target, ts, servername, text))
        self.irc.channels[target].topic = text
        self.irc.channels[target].topicset = True

    def inviteClient(self, numeric, target, channel):
        """Sends an INVITE from a PyLink client.."""
        if not utils.isInternalClient(self.irc, numeric):
            raise LookupError('No such PyLink PseudoClient exists.')
        self._send(numeric, 'INVITE %s %s' % (target, channel))

    def knockClient(self, numeric, target, text):
        """Sends a KNOCK from a PyLink client."""
        if not utils.isInternalClient(self.irc, numeric):
            raise LookupError('No such PyLink PseudoClient exists.')
        self._send(numeric, 'ENCAP * KNOCK %s :%s' % (target, text))

    def updateClient(self, numeric, field, text):
        """Updates the ident, host, or realname of a PyLink client."""
        field = field.upper()
        if field == 'IDENT':
            self.irc.users[numeric].ident = text
            self._send(numeric, 'FIDENT %s' % text)
        elif field == 'HOST':
            self.irc.users[numeric].host = text
            self._send(numeric, 'FHOST %s' % text)
        elif field in ('REALNAME', 'GECOS'):
            self.irc.users[numeric].realname = text
            self._send(numeric, 'FNAME :%s' % text)
        else:
            raise NotImplementedError("Changing field %r of a client is unsupported by this protocol." % field)

    def pingServer(self, source=None, target=None):
        """Sends a PING to a target server. Periodic PINGs are sent to our uplink
        automatically by the Irc() internals; plugins shouldn't have to use this."""
        source = source or self.irc.sid
        target = target or self.irc.uplink
        if not (target is None or source is None):
            self._send(source, 'PING %s %s' % (source, target))

    def numericServer(self, source, numeric, target, text):
        raise NotImplementedError("Numeric sending is not yet implemented by this "
                                  "protocol module. WHOIS requests are handled "
                                  "locally by InspIRCd servers, so there is no "
                                  "need for PyLink to send numerics directly yet.")

    def awayClient(self, source, text):
        """Sends an AWAY message from a PyLink client. <text> can be an empty string
        to unset AWAY status."""
        if text:
            self._send(source, 'AWAY %s :%s' % (int(time.time()), text))
        else:
            self._send(source, 'AWAY')

    def spawnServer(self, name, sid=None, uplink=None, desc=None):
        """
        Spawns a server off a PyLink server. desc (server description)
        defaults to the one in the config. uplink defaults to the main PyLink
        server, and sid (the server ID) is automatically generated if not
        given.
        """
        # -> :0AL SERVER test.server * 1 0AM :some silly pseudoserver
        uplink = uplink or self.irc.sid
        name = name.lower()
        # "desc" defaults to the configured server description.
        desc = desc or self.irc.serverdata.get('serverdesc') or self.irc.botdata['serverdesc']
        if sid is None:  # No sid given; generate one!
            sid = self.sidgen.next_sid()
        assert len(sid) == 3, "Incorrect SID length"
        if sid in self.irc.servers:
            raise ValueError('A server with SID %r already exists!' % sid)
        for server in self.irc.servers.values():
            if name == server.name:
                raise ValueError('A server named %r already exists!' % name)
        if not utils.isInternalServer(self.irc, uplink):
            raise ValueError('Server %r is not a PyLink internal PseudoServer!' % uplink)
        if not utils.isServerName(name):
            raise ValueError('Invalid server name %r' % name)
        self._send(uplink, 'SERVER %s * 1 %s :%s' % (name, sid, desc))
        self.irc.servers[sid] = IrcServer(uplink, name, internal=True, desc=desc)
        self._send(sid, 'ENDBURST')
        return sid

    def squitServer(self, source, target, text='No reason given'):
        """SQUITs a PyLink server."""
        # -> :9PY SQUIT 9PZ :blah, blah
        self._send(source, 'SQUIT %s :%s' % (target, text))
        self.handle_squit(source, 'SQUIT', [target, text])

    def connect(self):
        """Initializes a connection to a server."""
        ts = self.irc.start_ts

        f = self.irc.send
        f('CAPAB START 1202')
        f('CAPAB CAPABILITIES :PROTOCOL=1202')
        f('CAPAB END')
        f('SERVER {host} {Pass} 0 {sid} :{sdesc}'.format(host=self.irc.serverdata["hostname"],
          Pass=self.irc.serverdata["sendpass"], sid=self.irc.sid,
          sdesc=self.irc.serverdata.get('serverdesc') or self.irc.botdata['serverdesc']))
        f(':%s BURST %s' % (self.irc.sid, ts))
        f(':%s ENDBURST' % (self.irc.sid))

    def handle_events(self, data):
        """Event handler for the InspIRCd protocol.

        This passes most commands to the various handle_ABCD() functions
        elsewhere in this module, but also handles commands sent in the
        initial server linking phase."""
        # Each server message looks something like this:
        # :70M FJOIN #chat 1423790411 +AFPfjnt 6:5 7:5 9:5 :v,1SRAAESWE
        # :<sid> <command> <argument1> <argument2> ... :final multi word argument
        args = data.split(" ")
        if not args:
            # No data??
            return
        if args[0] == 'SERVER':
           # <- SERVER whatever.net abcdefgh 0 10X :something
           servername = args[1].lower()
           numeric = args[4]
           if args[2] != self.irc.serverdata['recvpass']:
                # Check if recvpass is correct
                raise ProtocolError('Error: recvpass from uplink server %s does not match configuration!' % servername)
           sdesc = ' '.join(args).split(':', 1)[1]
           self.irc.servers[numeric] = IrcServer(None, servername, desc=sdesc)
           self.irc.uplink = numeric
           return
        elif args[0] == 'CAPAB':
            # Capability negotiation with our uplink
            if args[1] == 'CHANMODES':
                # <- CAPAB CHANMODES :admin=&a allowinvite=A autoop=w ban=b banexception=e blockcolor=c c_registered=r exemptchanops=X filter=g flood=f halfop=%h history=H invex=I inviteonly=i joinflood=j key=k kicknorejoin=J limit=l moderated=m nickflood=F noctcp=C noextmsg=n nokick=Q noknock=K nonick=N nonotice=T official-join=!Y op=@o operonly=O opmoderated=U owner=~q permanent=P private=p redirect=L reginvite=R regmoderated=M secret=s sslonly=z stripcolor=S topiclock=t voice=+v

                # Named modes are essential for a cross-protocol IRC service. We
                # can use InspIRCd as a model here and assign a similar mode map to our cmodes list.
                for modepair in args[2:]:
                    name, char = modepair.split('=')
                    if name == 'reginvite':  # Reginvite? That's a dumb name.
                        name = 'regonly'
                    if name == 'founder':  # Channel mode +q
                        # Founder, owner; same thing. m_customprefix allows you to name it anything you like
                        # (the former is config default, but I personally prefer the latter.)
                        name = 'owner'
                    # We don't really care about mode prefixes; just the mode char
                    self.irc.cmodes[name.lstrip(':')] = char[-1]
            elif args[1] == 'USERMODES':
                # <- CAPAB USERMODES :bot=B callerid=g cloak=x deaf_commonchan=c helpop=h hidechans=I hideoper=H invisible=i oper=o regdeaf=R servprotect=k showwhois=W snomask=s u_registered=r u_stripcolor=S wallops=w
                # Ditto above.
                for modepair in args[2:]:
                    name, char = modepair.split('=')
                    self.irc.umodes[name.lstrip(':')] = char
            elif args[1] == 'CAPABILITIES':
                # <- CAPAB CAPABILITIES :NICKMAX=21 CHANMAX=64 MAXMODES=20 IDENTMAX=11 MAXQUIT=255 MAXTOPIC=307 MAXKICK=255 MAXGECOS=128 MAXAWAY=200 IP6SUPPORT=1 PROTOCOL=1202 PREFIX=(Yqaohv)!~&@%+ CHANMODES=IXbegw,k,FHJLfjl,ACKMNOPQRSTUcimnprstz USERMODES=,,s,BHIRSWcghikorwx GLOBOPS=1 SVSPART=1
                caps = dict([x.lstrip(':').split('=') for x in args[2:]])
                protocol_version = int(caps['PROTOCOL'])
                if protocol_version < 1202:
                    raise ProtocolError("Remote protocol version is too old! At least 1202 (InspIRCd 2.0.x) is needed. (got %s)" % protocol_version)
                self.irc.maxnicklen = int(caps['NICKMAX'])
                self.irc.maxchanlen = int(caps['CHANMAX'])
                # Modes are divided into A, B, C, and D classes
                # See http://www.irc.org/tech_docs/005.html

                # FIXME: Find a better way to assign/store this.
                self.irc.cmodes['*A'], self.irc.cmodes['*B'], self.irc.cmodes['*C'], self.irc.cmodes['*D'] \
                    = caps['CHANMODES'].split(',')
                self.irc.umodes['*A'], self.irc.umodes['*B'], self.irc.umodes['*C'], self.irc.umodes['*D'] \
                    = caps['USERMODES'].split(',')
                prefixsearch = re.search(r'\(([A-Za-z]+)\)(.*)', caps['PREFIX'])
                self.irc.prefixmodes = dict(zip(prefixsearch.group(1), prefixsearch.group(2)))
                log.debug('(%s) self.irc.prefixmodes set to %r', self.irc.name, self.irc.prefixmodes)
                # Sanity check: set this AFTER we fetch the capabilities for the network!
                self.irc.connected.set()
        try:
            args = self.parseTS6Args(args)
            numeric = args[0]
            command = args[1]
            args = args[2:]
        except IndexError:
            return

        # We will do wildcard event handling here. Unhandled events are just ignored.
        try:
            func = getattr(self, 'handle_'+command.lower())
        except AttributeError:  # unhandled event
            pass
        else:
            parsed_args = func(numeric, command, args)
            if parsed_args is not None:
                return [numeric, command, parsed_args]

    def handle_ping(self, source, command, args):
        """Handles incoming PING commands, so we don't time out."""
        # <- :70M PING 70M 0AL
        # -> :0AL PONG 0AL 70M
        if utils.isInternalServer(self.irc, args[1]):
            self._send(args[1], 'PONG %s %s' % (args[1], source))

    def handle_pong(self, source, command, args):
        """Handles incoming PONG commands.

        This is used to keep track of whether the uplink is alive by the Irc()
        internals - a server that fails to reply to our PINGs eventually
        times out and is disconnected."""
        if source == self.irc.uplink and args[1] == self.irc.sid:
            self.irc.lastping = time.time()

    def handle_fjoin(self, servernumeric, command, args):
        """Handles incoming FJOIN commands (InspIRCd equivalent of JOIN/SJOIN)."""
        # :70M FJOIN #chat 1423790411 +AFPfjnt 6:5 7:5 9:5 :o,1SRAABIT4 v,1IOAAF53R <...>
        channel = utils.toLower(self.irc, args[0])
        # InspIRCd sends each channel's users in the form of 'modeprefix(es),UID'
        userlist = args[-1].split()

        their_ts = int(args[1])
        our_ts = self.irc.channels[channel].ts
        self.updateTS(channel, their_ts)

        modestring = args[2:-1] or args[2]
        parsedmodes = utils.parseModes(self.irc, channel, modestring)
        utils.applyModes(self.irc, channel, parsedmodes)
        namelist = []
        for user in userlist:
            modeprefix, user = user.split(',', 1)
            namelist.append(user)
            self.irc.users[user].channels.add(channel)
            if their_ts <= our_ts:
                utils.applyModes(self.irc, channel, [('+%s' % mode, user) for mode in modeprefix])
            self.irc.channels[channel].users.add(user)
        return {'channel': channel, 'users': namelist, 'modes': parsedmodes, 'ts': their_ts}

    def handle_uid(self, numeric, command, args):
        """Handles incoming UID commands (user introduction)."""
        # :70M UID 70MAAAAAB 1429934638 GL 0::1 hidden-7j810p.9mdf.lrek.0000.0000.IP gl 0::1 1429934638 +Wioswx +ACGKNOQXacfgklnoqvx :realname
        uid, ts, nick, realhost, host, ident, ip = args[0:7]
        realname = args[-1]
        self.irc.users[uid] = IrcUser(nick, ts, uid, ident, host, realname, realhost, ip)
        parsedmodes = utils.parseModes(self.irc, uid, [args[8], args[9]])
        log.debug('Applying modes %s for %s', parsedmodes, uid)
        utils.applyModes(self.irc, uid, parsedmodes)
        self.irc.servers[numeric].users.add(uid)
        return {'uid': uid, 'ts': ts, 'nick': nick, 'realhost': realhost, 'host': host, 'ident': ident, 'ip': ip}

    def handle_server(self, numeric, command, args):
        """Handles incoming SERVER commands (introduction of servers)."""
        # SERVER is sent by our uplink or any other server to introduce others.
        # <- :00A SERVER test.server * 1 00C :testing raw message syntax
        # <- :70M SERVER millennium.overdrive.pw * 1 1ML :a relatively long period of time... (Fremont, California)
        servername = args[0].lower()
        sid = args[3]
        sdesc = args[-1]
        self.irc.servers[sid] = IrcServer(numeric, servername, desc=sdesc)
        return {'name': servername, 'sid': args[3], 'text': sdesc}

    def handle_fmode(self, numeric, command, args):
        """Handles the FMODE command, used for channel mode changes."""
        # <- :70MAAAAAA FMODE #chat 1433653462 +hhT 70MAAAAAA 70MAAAAAD
        channel = utils.toLower(self.irc, args[0])
        oldobj = self.irc.channels[channel].deepcopy()
        modes = args[2:]
        changedmodes = utils.parseModes(self.irc, channel, modes)
        utils.applyModes(self.irc, channel, changedmodes)
        ts = int(args[1])
        return {'target': channel, 'modes': changedmodes, 'ts': ts,
                'oldchan': oldobj}

    def handle_mode(self, numeric, command, args):
        """Handles incoming user mode changes."""
        # In InspIRCd, MODE is used for setting user modes and
        # FMODE is used for channel modes:
        # <- :70MAAAAAA MODE 70MAAAAAA -i+xc
        target = args[0]
        modestrings = args[1:]
        changedmodes = utils.parseModes(self.irc, numeric, modestrings)
        utils.applyModes(self.irc, target, changedmodes)
        return {'target': target, 'modes': changedmodes}

    def handle_idle(self, numeric, command, args):
        """Handles the IDLE command, sent between servers in remote WHOIS queries."""
        # <- :70MAAAAAA IDLE 1MLAAAAIG
        # -> :1MLAAAAIG IDLE 70MAAAAAA 1433036797 319
        sourceuser = numeric
        targetuser = args[0]
        self._send(targetuser, 'IDLE %s %s 0' % (sourceuser, self.irc.users[targetuser].ts))

    def handle_ftopic(self, numeric, command, args):
        """Handles incoming FTOPIC (sets topic on burst)."""
        # <- :70M FTOPIC #channel 1434510754 GLo|o|!GLolol@escape.the.dreamland.ca :Some channel topic
        channel = utils.toLower(self.irc, args[0])
        ts = args[1]
        setter = args[2]
        topic = args[-1]
        self.irc.channels[channel].topic = topic
        self.irc.channels[channel].topicset = True
        return {'channel': channel, 'setter': setter, 'ts': ts, 'topic': topic}

    # SVSTOPIC is used by InspIRCd module m_topiclock - its arguments are the same as FTOPIC
    handle_svstopic = handle_ftopic

    def handle_invite(self, numeric, command, args):
        """Handles incoming INVITEs."""
        # <- :70MAAAAAC INVITE 0ALAAAAAA #blah 0
        target = args[0]
        channel = utils.toLower(self.irc, args[1])
        # We don't actually need to process this; just send the hook so plugins can use it
        return {'target': target, 'channel': channel}

    def handle_encap(self, numeric, command, args):
        """Handles incoming encapsulated commands (ENCAP). Hook arguments
        returned by this should have a parse_as field, that sets the correct
        hook name for the message.

        For InspIRCd, the only ENCAP command we handle right now is KNOCK."""
        # <- :70MAAAAAA ENCAP * KNOCK #blah :agsdfas
        # From charybdis TS6 docs: https://github.com/grawity/self.irc-docs/blob/03ba884a54f1cef2193cd62b6a86803d89c1ac41/server/ts6.txt

        # ENCAP
        # source: any
        # parameters: target server mask, subcommand, opt. parameters...

        # Sends a command to matching servers. Propagation is independent of
        # understanding the subcommand.

        targetmask = args[0]
        real_command = args[1]
        if targetmask == '*' and real_command == 'KNOCK':
            channel = utils.toLower(self.irc, args[2])
            text = args[3]
            return {'parse_as': real_command, 'channel': channel,
                    'text': text}

    def handle_opertype(self, numeric, command, args):
        """Handles incoming OPERTYPE, which is used to denote an oper up.

        This calls the internal hook PYLINK_CLIENT_OPERED, sets the internal
        opertype of the client, and assumes setting user mode +o on the caller."""
        # This is used by InspIRCd to denote an oper up; there is no MODE
        # command sent for it.
        # <- :70MAAAAAB OPERTYPE Network_Owner
        omode = [('+o', None)]
        self.irc.users[numeric].opertype = opertype = args[0].replace("_", " ")
        utils.applyModes(self.irc, numeric, omode)
        # OPERTYPE is essentially umode +o and metadata in one command;
        # we'll call that too.
        self.irc.callHooks([numeric, 'PYLINK_CLIENT_OPERED', {'text': opertype}])
        return {'target': numeric, 'modes': omode}

    def handle_fident(self, numeric, command, args):
        """Handles FIDENT, used for denoting ident changes."""
        # <- :70MAAAAAB FIDENT test
        self.irc.users[numeric].ident = newident = args[0]
        return {'target': numeric, 'newident': newident}

    def handle_fhost(self, numeric, command, args):
        """Handles FHOST, used for denoting hostname changes."""
        # <- :70MAAAAAB FIDENT some.host
        self.irc.users[numeric].host = newhost = args[0]
        return {'target': numeric, 'newhost': newhost}

    def handle_fname(self, numeric, command, args):
        """Handles FNAME, used for denoting real name/gecos changes."""
        # <- :70MAAAAAB FNAME :afdsafasf
        self.irc.users[numeric].realname = newgecos = args[0]
        return {'target': numeric, 'newgecos': newgecos}

    def handle_endburst(self, numeric, command, args):
        """ENDBURST handler; sends a hook with empty contents."""
        return {}

    def handle_away(self, numeric, command, args):
        """Handles incoming AWAY messages."""
        # <- :1MLAAAAIG AWAY 1439371390 :Auto-away
        try:
            ts = args[0]
            self.irc.users[numeric].away = text = args[1]
            return {'text': text, 'ts': ts}
        except IndexError:  # User is unsetting away status
            self.irc.users[numeric].away = ''
            return {'text': ''}

Class = InspIRCdProtocol
