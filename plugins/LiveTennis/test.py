"""Exercise IRC replies, durable request limits and HTTP failure handling."""

import os
import io
import json
import time
import tempfile
import threading
import subprocess
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock
from concurrent.futures import ThreadPoolExecutor

import supybot.conf as conf
import supybot.ircmsgs as ircmsgs
from supybot.test import ChannelPluginTestCase

from . import cache
from . import plugin


def match(name='Player One', opponent='Player Two', tournament='Wimbledon',
          score=None):
    if score is None:
        score = {'games': [[6, 3], [4, 4]], 'points': ['40', '30'],
                 'server': 1, 'is_tiebreak': False, 'stale': False}
    return {'players': {'p1': {'name': name}, 'p2': {'name': opponent}},
            'tournament': tournament, 'score': score, 'status': 'live'}


def body(matches=None, partial=False):
    return json.dumps({'data': [match()] if matches is None else matches,
                       'meta': {'has_more': partial}}).encode('utf8')


def payload(matches=None, partial=False):
    return cache.decode_response(body(matches, partial))


class LiveTennisTestCase(ChannelPluginTestCase):
    plugins = ('LiveTennis',)
    config = {'supybot.plugins.LiveTennis.apiKey': 'test-key',
              'supybot.plugins.LiveTennis.refreshInterval': 900,
              'supybot.plugins.LiveTennis.maxMatches': 3}

    def setUp(self):
        super().setUp()
        self.clock = mock.Mock(return_value=1000000)
        clock = SimpleNamespace(time=self.clock)
        mock.patch.object(cache, 'time', clock).start()
        mock.patch.object(plugin, 'time', clock).start()
        self.fetch = mock.patch.object(cache, 'fetch_matches',
                                       return_value=payload()).start()
        self.environment = mock.patch.dict(
            os.environ, {'LIVETENNIS_API_KEY': ''})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        mock.patch.stopall()
        super().tearDown()

    def reply(self, command='tennis', **kwargs):
        return self.getMsg(command, **kwargs).args[1]

    def testPlayerMajorScoreAndServe(self):
        result = self.reply()
        self.assertIn('Snapshot age 0s.', result)
        self.assertIn('Player One* vs Player Two', result)
        self.assertIn('6-4 3-4, points 40-30', result)
        self.assertIn('* serving.', result)
        self.fetch.assert_called_once_with('test-key')

    def testFiltersAndChannelsShareSnapshot(self):
        self.reply()
        self.irc.feedMsg(ircmsgs.join('#tennis', prefix=self.prefix))
        for command in ('MODE', 'MODE', 'WHO'):
            self.assertEqual(self.irc.takeMsg().command, command)
        self.assertIn('Player One', self.reply('tennis One', to='#tennis'))
        self.assertIn('Player One', self.reply('livetennis tennis Wimbledon'))
        self.assertIn('No matches for this filter', self.reply('tennis missing'))
        self.assertEqual(self.fetch.call_count, 1)

    def testEmptySnapshot(self):
        self.fetch.return_value = payload([])
        self.assertIn('Snapshot age 0s. No live matches', self.reply())

    def testNullableScorePointsAndWithheldGames(self):
        null_score = match('No Score')
        null_score['score'] = None
        missing_points = match('No Points', score={
            'games': [[0], [0]], 'points': [None, None], 'server': None})
        withheld = match('Withheld', score={'games': None, 'points': [],
                                           'stale': True})
        self.fetch.return_value = payload([null_score, missing_points, withheld])
        self.assertIn('score unavailable', self.reply('tennis No Score'))
        self.assertIn('0-0, points unknown', self.reply('tennis No Points'))
        result = self.reply('tennis Withheld')
        self.assertIn('score unavailable', result)
        self.assertIn('(feed stale)', result)
        self.assertNotIn('0-0', result)

    def testFeedStaleAndMatchTiebreak(self):
        self.fetch.return_value = payload([match(score={
            'games': [[6, 4, 10], [4, 6, 5]], 'points': ['10', '5'],
            'is_tiebreak': True, 'server': None, 'stale': True})])
        result = self.reply()
        self.assertIn('6-4 4-6 [10-5]', result)
        self.assertIn('tiebreak points 10-5', result)
        self.assertIn('(feed stale)', result)

    def testEarlyMatchTiebreakAndOrdinaryTiebreak(self):
        early = match('Early', score={
            'games': [[6, 4, 3], [4, 6, 4]], 'points': ['3', '4'],
            'is_tiebreak': True})
        ordinary = match('Ordinary', score={
            'games': [[6], [6]], 'points': ['3', '4'], 'is_tiebreak': True})
        self.fetch.return_value = payload([early, ordinary])
        self.assertIn('6-4 4-6 [3-4], tiebreak points 3-4',
                      self.reply('tennis Early'))
        self.assertIn('6-6, tiebreak points 3-4', self.reply('tennis Ordinary'))

    def testMissingAndInvalidKeyMakeNoRequest(self):
        setting = conf.supybot.plugins.LiveTennis.apiKey
        setting.setValue('')
        self.assertError('tennis')
        setting.setValue('bad\nkey')
        self.assertError('tennis')
        self.fetch.assert_not_called()
        self.assertTrue(setting._private)

    def testEnvironmentFallbackAndConfigPrecedence(self):
        os.environ['LIVETENNIS_API_KEY'] = 'environment-key'
        self.reply()
        self.fetch.assert_called_once_with('test-key')
        conf.supybot.plugins.LiveTennis.apiKey.setValue('')
        self.reply()
        self.assertEqual(self.fetch.call_args, mock.call('environment-key'))

    def testFailedRefreshPreservesStaleSnapshotAndFloor(self):
        self.reply()
        self.clock.return_value += 900
        self.fetch.side_effect = cache.FetchError('API returned HTTP 429.')
        result = self.reply()
        self.assertIn('Stale snapshot age 15m.', result)
        self.assertIn('HTTP 429', result)
        self.assertIn('6-4 3-4', result)
        self.clock.return_value += 899
        self.reply()
        self.assertEqual(self.fetch.call_count, 2)

    def testFailedFirstFetchIsSafeAndWaits(self):
        self.fetch.side_effect = cache.FetchError('Invalid API response.')
        result = self.assertError('tennis').args[1]
        self.assertIn('No snapshot available.', result)
        self.assertNotIn('test-key', result)
        self.assertError('tennis')
        self.fetch.assert_called_once()

    def testConfiguredShortIntervalIsClamped(self):
        conf.supybot.plugins.LiveTennis.refreshInterval.setValue(1)
        self.reply()
        self.clock.return_value += 899
        self.reply()
        self.assertEqual(self.fetch.call_count, 1)
        self.clock.return_value += 1
        self.reply()
        self.assertEqual(self.fetch.call_count, 2)

    def testReplyIsBoundedAndReportsPartialPage(self):
        self.fetch.return_value = payload([
            match('A' * 200, 'B' * 200, 'C' * 200) for _ in range(10)], True)
        conf.supybot.plugins.LiveTennis.maxMatches.setValue(500)
        result = self.reply()
        self.assertIn('more matching rows.', result)
        self.assertIn('First page only.', result)
        self.assertLessEqual(len(result.encode('utf8')), 350)

    def testControlCharactersCannotCreateExtraReplies(self):
        self.fetch.return_value = payload([match(
            'One\r\n\x01\x02', 'Two\x0f', 'Wimbledon\x03')])
        result = self.reply()
        self.assertNotRegex(result, r'[\x00-\x1f\x7f-\x9f]')
        self.assertIn('One* vs Two', result)

    def testUnavailableCacheDoesNotRequest(self):
        with mock.patch.object(cache.SnapshotCache, '_connect',
                               side_effect=cache.sqlite3.OperationalError()):
            self.assertError('tennis')
        self.fetch.assert_not_called()


class SnapshotCacheTestCase(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, 'snapshots.sqlite3')
        self.clock = mock.patch.object(time, 'time', return_value=1000000).start()
        self.fetch = mock.patch.object(cache, 'fetch_matches',
                                       return_value=payload()).start()
        self.addCleanup(mock.patch.stopall)

    def testRestartAndBackwardClockRetainFloor(self):
        cache.SnapshotCache(self.path).snapshot('secret')
        self.clock.return_value -= 10000
        cache.SnapshotCache(self.path).snapshot('secret')
        self.clock.return_value = 1000899
        cache.SnapshotCache(self.path).snapshot('secret')
        self.fetch.assert_called_once()
        self.clock.return_value += 1
        cache.SnapshotCache(self.path).snapshot('secret')
        self.assertEqual(self.fetch.call_count, 2)

    def testKeyIsHashedAndSnapshotsAreSeparate(self):
        cache.SnapshotCache(self.path).snapshot('first-secret')
        cache.SnapshotCache(self.path).snapshot('second-secret')
        self.assertEqual(self.fetch.call_count, 2)
        contents = Path(self.path).read_bytes()
        self.assertNotIn(b'first-secret', contents)
        self.assertNotIn(b'second-secret', contents)

    def testConcurrentRequestReservesFloorBeforeHTTP(self):
        started = threading.Event()
        finish = threading.Event()

        def fetch(key):
            started.set()
            self.assertTrue(finish.wait(5))
            return payload()

        self.fetch.side_effect = fetch
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(cache.SnapshotCache(self.path).snapshot,
                                    'secret')
            self.assertTrue(started.wait(5))
            pending = cache.SnapshotCache(self.path).snapshot('secret')
            self.assertIsNone(pending['payload'])
            self.assertEqual(pending['error'], 'Refresh pending.')
            finish.set()
            self.assertIsNotNone(first.result(5)['payload'])
        self.fetch.assert_called_once()

    def testPausedCompletionCannotOverwriteNewerSnapshot(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                state = cache.SnapshotCache(self.path + str(failed))
                self.clock.return_value = 1000000
                calls = []

                def fetch(key):
                    calls.append(key)
                    if len(calls) == 1:
                        self.clock.return_value += 900
                        state.snapshot(key)
                        if failed:
                            raise cache.FetchError('Older request failed.')
                        return payload([match('Older')])
                    return payload([match('Newer')])

                self.fetch.side_effect = fetch
                result = state.snapshot('secret')
                self.assertEqual(len(calls), 2)
                self.assertEqual(result['fetched'], 1000900)
                self.assertIsNone(result['error'])
                self.assertEqual(result['payload']['matches'][0]['names'][0],
                                 'Newer')

    def testFreeDailyBudgetIncludesFailures(self):
        self.fetch.side_effect = cache.FetchError('API request failed.')
        state = cache.SnapshotCache(self.path)
        for seconds in range(0, 86400, 300):
            self.clock.return_value = 1000000 + seconds
            state.snapshot('secret', interval=1)
        self.assertEqual(self.fetch.call_count, 96)

    def testLongerConfiguredInterval(self):
        state = cache.SnapshotCache(self.path)
        state.snapshot('secret', interval=3600)
        self.clock.return_value += 3599
        state.snapshot('secret', interval=3600)
        self.fetch.assert_called_once()

    def testTwoProcessesShareAttemptReservation(self):
        source = str(Path(cache.__file__).resolve())
        program = (
            'import importlib.util,sys,time; '
            'spec=importlib.util.spec_from_file_location("cache",sys.argv[1]); '
            'module=importlib.util.module_from_spec(spec); '
            'spec.loader.exec_module(module); '
            'module.time.time=lambda:1000000; '
            'module.fetch_matches=lambda key: '
            '(open(sys.argv[3],"a").write("request\\n") and '
            '{"matches":[],"partial":False}); '
            'module.SnapshotCache(sys.argv[2]).snapshot("process-secret")')
        marker = os.path.join(self.directory.name, 'requests')
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(subprocess.run,
                       [os.sys.executable, '-c', program, source, self.path,
                        marker], capture_output=True, timeout=10)
                       for _ in range(2)]
            for future in futures:
                result = future.result()
                self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(Path(marker).read_text().splitlines(), ['request'])


class HTTPTestCase(TestCase):
    def testHeaderFixedEndpointAndBoundedRead(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = body()
        with mock.patch.object(urllib.request, 'build_opener') as build:
            build.return_value.open.return_value = response
            result = cache.fetch_matches('header-secret')
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, cache.ENDPOINT)
        self.assertEqual(request.get_header('X-api-key'), 'header-secret')
        self.assertNotIn('header-secret', request.full_url)
        self.assertEqual(build.return_value.open.call_args.kwargs,
                         {'timeout': 10})
        response.read.assert_called_once_with(cache.MAX_BYTES + 1)
        self.assertIsInstance(build.call_args.args[0], cache.NoRedirect)
        self.assertEqual(len(result['matches']), 1)

    def testHTTPFailuresDoNotLeakBodiesOrKeys(self):
        for code in (301, 302, 307, 308, 401, 403, 429, 500):
            with self.subTest(code=code):
                response = io.BytesIO(b'header-secret response')
                error = urllib.error.HTTPError(
                    cache.ENDPOINT, code, 'header-secret', {}, response)
                with mock.patch.object(urllib.request, 'build_opener') as build:
                    build.return_value.open.side_effect = error
                    with self.assertRaises(cache.FetchError) as caught:
                        cache.fetch_matches('header-secret')
                self.assertNotIn('header-secret', str(caught.exception))
                self.assertTrue(response.closed)
        self.assertIsNone(cache.NoRedirect().redirect_request(
            None, None, 302, '', {}, 'https://example.invalid/'))

    def testTimeoutAndBrokenResponse(self):
        failures = [TimeoutError('secret'), urllib.error.URLError('secret'),
                    cache.http.client.IncompleteRead(b'secret')]
        for error in failures:
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(urllib.request, 'build_opener') as build:
                    build.return_value.open.side_effect = error
                    with self.assertRaisesRegex(cache.FetchError,
                                                '^API request failed.$'):
                        cache.fetch_matches('secret')

    def testMalformedResponsesAreRejected(self):
        invalid = [b'not json', b'\xff', b'[]', b'{"data":{}}',
                   b'{"data":[{}]}', body(partial='yes')]
        broken = match(score={'games': [[6, 2], [4]], 'points': ['40', '30']})
        invalid.append(body([broken]))
        broken = match(score={'games': [[True], [0]], 'points': []})
        invalid.append(body([broken]))
        for raw in invalid:
            with self.subTest(raw=raw[:50]):
                with self.assertRaisesRegex(cache.FetchError,
                                            '^Invalid API response.$'):
                    cache.decode_response(raw)

    def testOversizedResponseIsRejected(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b' ' * (cache.MAX_BYTES + 1)
        with mock.patch.object(urllib.request, 'build_opener') as build:
            build.return_value.open.return_value = response
            with self.assertRaisesRegex(cache.FetchError, 'too large'):
                cache.fetch_matches('secret')

    def testClockRollbackIsVisibleInOutput(self):
        snapshot = {'payload': payload(), 'fetched': 200, 'attempted': 200,
                    'error': None}
        result = plugin.reply_text(snapshot, None, 3, 100)
        self.assertIn('Stale snapshot age unknown.', result)
