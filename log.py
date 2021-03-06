"""
log.py - PyLink logging module.

This module contains the logging portion of the PyLink framework. Plugins can
access the global logger object by importing "log" from this module
(from log import log).
"""

import logging
import sys
import os

from conf import conf, confname

level = conf['bot'].get('loglevel') or 'DEBUG'
try:
    level = getattr(logging, level.upper())
except AttributeError:
    print('ERROR: Invalid log level %r specified in config.' % level)
    sys.exit(3)

curdir = os.path.dirname(os.path.realpath(__file__))
logdir = os.path.join(curdir, 'log')
# Make sure our log/ directory exists
os.makedirs(logdir, exist_ok=True)

_format = '%(asctime)s [%(levelname)s] %(message)s'
logging.basicConfig(level=level, format=_format)

# Set log file to $CURDIR/log/pylink
logformat = logging.Formatter(_format)
logfile = logging.FileHandler(os.path.join(logdir, '%s.log' % confname), mode='w')
logfile.setFormatter(logformat)

global log
log = logging.getLogger()
log.addHandler(logfile)
