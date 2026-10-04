LiveTennis shows cached tennis score snapshots with `tennis [text]`.

It needs a free Live Tennis API key. Set `LIVETENNIS_API_KEY` in the bot environment, then load LiveTennis.
The private global setting `plugins.LiveTennis.apiKey` also accepts the key.

Player or tournament text filters share the same cache as unfiltered commands.
Requests are at least 15 minutes apart, including failures and bot restarts.
Every reply includes the snapshot age. Saved scores are marked stale after a failed refresh.

The plugin uses the standard library and Limnoria. No extra package is needed.
Installation and checks are at https://github.com/livetennisapi/limnoria-livetennis.
