"""Show cached score snapshots without polling for each channel or filter."""

import os
import re
import time
import sqlite3

import supybot.conf as conf
import supybot.callbacks as callbacks
from supybot.commands import wrap, optional
from supybot.i18n import PluginInternationalization

from . import cache

_ = PluginInternationalization('LiveTennis')
MAX_REPLY_BYTES = 350


def clean_text(value, limit=40):
    value = re.sub(r'[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e]',
                   ' ', str(value))
    return ' '.join(value.split())[:limit]


def duration(seconds):
    if seconds < 60:
        return '%ss' % int(seconds)
    if seconds < 3600:
        return '%sm' % int(seconds // 60)
    return '%sh' % int(seconds // 3600)


def match_text(match):
    names = [clean_text(name) for name in match['names']]
    score = match['score']
    if score and score.get('server'):
        names[score['server'] - 1] += '*'
    text = '%s vs %s (%s)' % (*names, clean_text(match['tournament'], 25))
    if not score or score.get('games') is None:
        text += ': score unavailable'
        if score and score.get('stale'):
            text += ' (feed stale)'
        return text
    games = score['games']
    sets = ['%s-%s' % (a, b) for a, b in zip(*games)] if games else []
    if (sets and score.get('is_tiebreak') and
            (games[0][-1], games[1][-1]) != (6, 6)):
        sets[-1] = '[' + sets[-1] + ']'
    text += ': ' + (' '.join(sets) or 'set score unavailable')
    points = score.get('points') or []
    if len(points) == 2 and all(p is not None for p in points):
        label = 'tiebreak points' if score.get('is_tiebreak') else 'points'
        text += ', %s %s-%s' % (label, clean_text(points[0], 5),
                               clean_text(points[1], 5))
    else:
        text += ', points unknown'
    if score.get('stale'):
        text += ' (feed stale)'
    return text


def reply_text(snapshot, query, limit, now):
    fetched = snapshot['fetched']
    if fetched is None or now < fetched:
        age = 'unknown'
    else:
        age = duration(now - fetched)
    stale = snapshot['error'] or now < fetched or now - fetched >= 900
    prefix = '%s age %s. ' % ('Stale snapshot' if stale else 'Snapshot', age)
    if snapshot['error']:
        prefix += snapshot['error'] + ' '
    payload = snapshot['payload']
    needle = clean_text(query or '', 100).casefold()
    matches = [m for m in payload['matches'] if needle in
               ' '.join(clean_text(n, 200) for n in
                        m['names'] + [m['tournament']]).casefold()]
    partial = ' First page only.' if payload['partial'] else ''
    if not matches:
        text = prefix + ('No matches for this filter in the snapshot.'
                         if needle else 'No live matches in the snapshot.')
        text += partial
    else:
        parts = []
        serving = ''
        for match in matches[:max(1, min(5, limit))]:
            part = match_text(match)
            marker = (' * serving.' if '*' in part or serving else '')
            remaining = len(matches) - len(parts) - 1
            omitted = ('. %s more matching rows.' % remaining
                       if remaining else '')
            candidate = (prefix + ' | '.join(parts + [part]) + omitted +
                         marker + partial)
            if len(candidate.encode('utf8')) > MAX_REPLY_BYTES:
                break
            parts.append(part)
            serving = marker
        if not parts:
            part = match_text(matches[0])
            serving = ' * serving.' if '*' in part else ''
            suffix_size = len((prefix + serving + partial).encode('utf8')) + 40
            part = part.encode('utf8')[:MAX_REPLY_BYTES - suffix_size]
            parts.append(part.decode('utf8', errors='ignore') + '...')
        text = prefix + ' | '.join(parts)
        if len(matches) > len(parts):
            text += '. %s more matching rows.' % (len(matches) - len(parts))
        text += serving + partial
    return text.encode('utf8')[:MAX_REPLY_BYTES].decode('utf8', errors='ignore')


class LiveTennis(callbacks.Plugin):
    """Use tennis [text] for snapshots filtered by player or tournament."""
    threaded = True

    def tennis(self, irc, msg, args, text):
        """[text]

        Show cached live score snapshots. Filter by player or tournament.
        Requests are at least 15 minutes apart. An asterisk marks the server.
        """
        api_key = (self.registryValue('apiKey') or
                   os.environ.get('LIVETENNIS_API_KEY', '')).strip()
        if not api_key:
            irc.error(_('Set the private LiveTennis apiKey or '
                        'LIVETENNIS_API_KEY first.'))
            return
        if (len(api_key) > 512 or not api_key.isascii() or
                any(ord(c) < 33 or ord(c) > 126 for c in api_key)):
            irc.error(_('The configured API key is invalid.'))
            return
        path = os.path.join(conf.supybot.directories.data(), 'LiveTennis.sqlite3')
        try:
            snapshot = cache.SnapshotCache(path).snapshot(
                api_key, self.registryValue('refreshInterval'))
        except (sqlite3.Error, OSError, ValueError):
            irc.error(_('Snapshot cache unavailable.'))
            return
        if snapshot['payload'] is None:
            retry = max(0, snapshot['attempted'] + max(
                900, self.registryValue('refreshInterval')) - time.time())
            irc.error(_('No snapshot available. %s Retry in %s.') %
                      (snapshot['error'], duration(retry)))
            return
        irc.reply(reply_text(snapshot, text, self.registryValue('maxMatches'),
                             time.time()))

    tennis = wrap(tennis, [optional('text')])


Class = LiveTennis
