"""Persist shared snapshots and reserve requests before contacting the API."""

import json
import time
import hashlib
import sqlite3
import http.client
import urllib.error
import urllib.request


ENDPOINT = ('https://api.livetennisapi.com/api/public/v1/'
            'matches?status=live&limit=200')
MIN_INTERVAL = 900
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 10


class FetchError(Exception):
    """A public error message without response bodies or credentials."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        return None


def _numbers(value):
    return (isinstance(value, list) and len(value) <= 5 and
            all(type(n) is int and 0 <= n <= 1000000 for n in value))


def decode_response(raw):
    """Keep the displayed fields and reject unusable snapshots."""
    try:
        response = json.loads(raw)
        data = response['data']
        if not isinstance(data, list) or len(data) > 200:
            raise ValueError()
        meta = response.get('meta', {})
        partial = meta.get('has_more', len(data) == 200)
        if type(partial) is not bool:
            raise ValueError()
        matches = []
        for match in data:
            names = [match['players'][side]['name'] for side in ('p1', 'p2')]
            tournament = match['tournament']
            if (not all(isinstance(n, str) and n.strip() for n in names) or
                    not isinstance(tournament, str)):
                raise ValueError()
            score = match.get('score')
            if score is not None:
                games = score.get('games')
                points = score.get('points', [])
                if games not in (None, []):
                    if (not isinstance(games, list) or len(games) != 2 or
                            not all(_numbers(g) for g in games) or
                            len(games[0]) != len(games[1])):
                        raise ValueError()
                if (not isinstance(points, list) or len(points) not in (0, 2)
                        or not all(p is None or isinstance(p, str)
                                   for p in points)):
                    raise ValueError()
                server = score.get('server')
                if server is not None and (type(server) is not int or
                                           server not in (1, 2)):
                    raise ValueError()
                if (type(score.get('is_tiebreak', False)) is not bool or
                        type(score.get('stale', False)) is not bool):
                    raise ValueError()
                score = {name: score.get(name) for name in
                         ('games', 'points', 'server', 'is_tiebreak', 'stale')}
            matches.append({'names': names, 'tournament': tournament,
                            'score': score})
        return {'matches': matches, 'partial': partial}
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        raise FetchError('Invalid API response.') from None


def fetch_matches(api_key):
    request = urllib.request.Request(
        ENDPOINT, headers={'X-API-Key': api_key, 'Accept': 'application/json'})
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise FetchError('API response is too large.')
            return decode_response(raw)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        if 300 <= code < 400:
            raise FetchError('API redirect refused.') from None
        raise FetchError('API returned HTTP %s.' % code) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError,
            http.client.HTTPException):
        raise FetchError('API request failed.') from None


class SnapshotCache:
    def __init__(self, path):
        self.path = path

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=20)
        try:
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute(
                'CREATE TABLE IF NOT EXISTS snapshots ('
                'key_id TEXT PRIMARY KEY, attempted REAL NOT NULL, '
                'fetched REAL, payload TEXT, error TEXT)')
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    def snapshot(self, api_key, interval=MIN_INTERVAL):
        key_id = hashlib.sha256(api_key.encode('utf8')).hexdigest()
        # 86,400 / 900 = 96 attempts per day, including failed requests.
        interval = max(MIN_INTERVAL, interval)
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            now = time.time()
            row = connection.execute(
                'SELECT attempted, fetched, payload, error FROM snapshots '
                'WHERE key_id=?', (key_id,)).fetchone()
            if row and now - row[0] < interval:
                connection.commit()
                return self._snapshot(row)
            connection.execute(
                'INSERT INTO snapshots VALUES (?, ?, NULL, NULL, ?) '
                'ON CONFLICT(key_id) DO UPDATE SET attempted=excluded.attempted, '
                'error=excluded.error', (key_id, now, 'Refresh pending.'))
            # Commit before HTTP so another process and a restart see the floor.
            connection.commit()
            try:
                payload = fetch_matches(api_key)
            except FetchError as error:
                connection.execute(
                    'UPDATE snapshots SET error=? WHERE key_id=? AND attempted=?',
                    (str(error), key_id, now))
            else:
                connection.execute(
                    'UPDATE snapshots SET fetched=?, payload=?, error=NULL '
                    'WHERE key_id=? AND attempted=?',
                    (time.time(), json.dumps(payload), key_id, now))
            connection.commit()
            row = connection.execute(
                'SELECT attempted, fetched, payload, error FROM snapshots '
                'WHERE key_id=?', (key_id,)).fetchone()
            return self._snapshot(row)
        finally:
            connection.close()

    @staticmethod
    def _snapshot(row):
        payload = json.loads(row[2]) if row[2] else None
        if payload is not None and row[1] is None:
            raise ValueError('Snapshot timestamp missing.')
        return {'attempted': row[0], 'fetched': row[1], 'payload': payload,
                'error': row[3]}
