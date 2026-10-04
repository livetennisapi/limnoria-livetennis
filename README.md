# Tennis score snapshots for Limnoria

LiveTennis adds `tennis [text]` to a Limnoria IRC bot.
It shows cached live match scores and the age of the snapshot.
The text filter matches player names or tournament names.

A free Live Tennis API key covers this command. It uses `GET /matches?status=live&limit=200`.
No paid endpoint or plan is required.

## Install

Clone this repository alongside your bot:

```sh
git clone https://github.com/livetennisapi/limnoria-livetennis.git
```

Add the cloned `plugins` directory to `supybot.directories.plugins`.
Set `LIVETENNIS_API_KEY` in the bot's environment before starting it.
Then run `load LiveTennis` through your usual bot command prefix.

The private global setting `plugins.LiveTennis.apiKey` can also hold the key.
Use a private conversation for configuration. The registry setting takes precedence over the environment.

After the repository is registered in PluginDownloader, installation is also available through:

```text
plugindownloader install livetennisapi LiveTennis
load LiveTennis
```

The plugin uses Python's standard library and Limnoria. It needs no extra package.

## Commands

```text
tennis
tennis Swiatek
tennis Wimbledon
```

The default output shows at most three matches in one reply.
`plugins.LiveTennis.maxMatches` changes that count, capped at five.
Long replies are shortened to fit IRC. The output reports omitted matches and incomplete API pages.

Scores read the API's player rows correctly: `[[6,3],[4,4]]` displays `6-4 3-4`.
Missing scores and points stay unknown. Refresh errors label saved scores stale.

## Request budget

`plugins.LiveTennis.refreshInterval` defaults to 900 seconds.
The code applies a minimum of 900 seconds even if configuration asks for faster updates.
There is no forced refresh command.

Commands, filters and channels share one saved snapshot in the bot's data directory.
A SQLite transaction reserves each request before the HTTP call.
The request floor survives restarts and applies to processes sharing that data directory.
Failed requests also count. At most 96 attempts fit in a day at this cadence.
Other clients using the same API key also consume its daily quota.

The cache contains a hash of the key, timestamps and response data. It never stores the key itself.
The HTTPS request sends the key in `X-API-Key`. Redirects are refused.

## Checks

With Limnoria installed, run:

```sh
supybot-test LiveTennis -v --plugins-dir=./plugins/ --no-network
```

The tests cover IRC commands, score formatting, persisted caching and request failures.
They use local fixtures and mocked HTTP responses. An authenticated live API check needs an existing key.

The host's [plugin distribution guide](https://docs.limnoria.net/develop/plugin_distribution.html) describes this repository format.
The API contract is in the [OpenAPI specification](https://docs.livetennisapi.com/openapi.yaml).

This plugin is available under the [0BSD licence](LICENSE).
