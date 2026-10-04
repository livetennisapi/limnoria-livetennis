"""Read cached live tennis score snapshots."""

import supybot.world as world

from . import config
from . import plugin

if world.testing:
    from . import test

Class = plugin.Class
configure = config.configure
