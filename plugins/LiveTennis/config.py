"""Configure credentials and bounded snapshot output."""

import supybot.conf as conf
import supybot.registry as registry


def configure(advanced):
    conf.registerPlugin('LiveTennis', True)


LiveTennis = conf.registerPlugin('LiveTennis')
conf.registerGlobalValue(
    LiveTennis, 'apiKey', registry.String(
        '', 'API key. Leave empty to use LIVETENNIS_API_KEY.', private=True))
conf.registerGlobalValue(
    LiveTennis, 'refreshInterval', registry.PositiveInteger(
        900, 'Seconds between requests. Values below 900 use 900 seconds.'))
conf.registerGlobalValue(
    LiveTennis, 'maxMatches', registry.PositiveInteger(
        3, 'Matches per reply. Values above five use five.'))
