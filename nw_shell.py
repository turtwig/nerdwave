#!/usr/bin/env python

from traitlets.config import Config

import IPython

from libs import db
from libs import cache
from libs import config
from libs import log

config.load()
cache.connect()
log.init()
db.connect()

c = Config()
c.InteractiveShellApp.exec_lines = [
    "from libs import db",
    "from libs import cache",
    "from libs import config",
    "from nerdwave.events.election import Election, ElectionProducer",
    "from nerdwave.events.oneup import OneUp, OneUpProducer",
    "from nerdwave.playlist_objects.album import Album",
    "from nerdwave.playlist_objects.artist import Artist",
    "from nerdwave.playlist_objects.song import Song",
    "from nerdwave.playlist_objects.songgroup import SongGroup",
    "from nerdwave.user import User",
]
c.TerminalIPythonApp.display_banner = False

IPython.start_ipython(config=c)
