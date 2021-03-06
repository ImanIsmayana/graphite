#!/usr/bin/env python
"""Copyright 2009 Chris Davis

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License."""

import sys
import os
import socket
import pwd
import optparse
import atexit
from os.path import basename, dirname, exists, join, isdir


program = basename( sys.argv[0] ).split('.')[0]
hostname = socket.gethostname().split('.')[0]
os.umask(022)

# Initialize twisted
try:
  from twisted.internet import epollreactor
  epollreactor.install()
except:
  pass
from twisted.internet import reactor


# Figure out where we're installed
BIN_DIR = dirname( os.path.abspath(__file__) )
ROOT_DIR = dirname(BIN_DIR)
STORAGE_DIR = join(ROOT_DIR, 'storage')
LOG_DIR = join(STORAGE_DIR, 'log', 'carbon-cache')
LIB_DIR = join(ROOT_DIR, 'lib')
CONF_DIR = join(ROOT_DIR, 'conf')
__builtins__.CONF_DIR = CONF_DIR # evil I know, but effective.

sys.path.insert(0, LIB_DIR)

# Capture useful debug info for this commonly reported problem
try:
  import carbon
except ImportError:
  print 'Failed to import carbon, debug information follows.'
  print 'pwd=%s' % os.getcwd()
  print 'sys.path=%s' % sys.path
  print '__file__=%s' % __file__
  sys.exit(1)


# Parse command line options
parser = optparse.OptionParser(usage='%prog [options] <start|stop|status>')
parser.add_option('--debug', action='store_true', help='Run in the foreground, log to stdout')
parser.add_option('--profile', help='Record performance profile data to the given file')
parser.add_option('--pidfile', default=join(STORAGE_DIR, '%s.pid' % program), help='Write pid to the given file')
parser.add_option('--config', default=join(CONF_DIR, 'carbon.conf'), help='Use the given config file')
parser.add_option('--logdir', default=LOG_DIR, help="Write logs in the given directory")
parser.add_option('--instance', default=None, help="Manage a specific carbon instnace")

(options, args) = parser.parse_args()

if not args:
  parser.print_usage()
  raise SystemExit(1)

# Assume standard locations when using --instance
if options.instance:
  instance = str(options.instance)
  pidfile = join(STORAGE_DIR, '%s-%s.pid' % (program, instance))
  logdir = "%s-%s/" % (LOG_DIR.rstrip('/'), instance)
  config = options.config
else:
  instance = None
  pidfile = options.pidfile
  logdir = options.logdir
  config = options.config

__builtins__.instance = instance # This isn't as evil as you might think
__builtins__.program = program
action = args[0]

if action == 'stop':
  if not exists(pidfile):
    print 'Pidfile %s does not exist' % pidfile
    raise SystemExit(0)

  pf = open(pidfile, 'r')
  try:
    pid = int( pf.read().strip() )
  except:
    print 'Could not read pidfile %s' % pidfile
    raise SystemExit(1)

  print 'Deleting %s (contained pid %d)' % (pidfile, pid)
  os.unlink(pidfile)

  print 'Sending kill signal to pid %d' % pid
  os.kill(pid, 15)
  raise SystemExit(0)


elif action == 'status':
  if not exists(pidfile):
    print '%s (instance %s) is not running' % (program, instance)
    raise SystemExit(0)

  pf = open(pidfile, 'r')
  try:
    pid = int( pf.read().strip() )
  except:
    print 'Failed to read pid from %s' % pidfile
    raise SystemExit(1)

  if exists('/proc/%d' % pid):
    print "%s (instance %s) is running with pid %d" % (program, instance, pid)
    raise SystemExit(0)
  else:
    print "%s (instance %s) is not running" % (program, instance)
    raise SystemExit(0)

elif action != 'start':
  parser.print_usage()
  raise SystemExit(1)


if exists(pidfile):
  print "Pidfile %s already exists, is %s already running?" % (pidfile, program)
  raise SystemExit(1)


# Read config (we want failures to occur before daemonizing)
from carbon.conf import settings
settings.readFrom(config, 'cache')

if instance:
  settings.readFrom(config, 'cache:%s' % instance)

# Import application components
from carbon.log import logToStdout, logToDir
from carbon.listeners import MetricLineReceiver, MetricPickleReceiver, CacheQueryHandler, startListener
from carbon.cache import MetricCache
from carbon.instrumentation import startRecording
from carbon.events import metricReceived

storage_schemas = join(CONF_DIR, 'storage-schemas.conf')
if not exists(storage_schemas):
  print "Error: missing required config %s" % storage_schemas
  sys.exit(1)

use_amqp = settings.get("ENABLE_AMQP", False)
if use_amqp:
  from carbon import amqp_listener
  amqp_host = settings.get("AMQP_HOST", "localhost")
  amqp_port = settings.get("AMQP_PORT", 5672)
  amqp_user = settings.get("AMQP_USER", "guest")
  amqp_password = settings.get("AMQP_PASSWORD", "guest")
  amqp_verbose  = settings.get("AMQP_VERBOSE", False)
  amqp_vhost    = settings.get("AMQP_VHOST", "/")
  amqp_spec     = settings.get("AMQP_SPEC", None)
  amqp_exchange_name = settings.get("AMQP_EXCHANGE", "graphite")


# --debug
if options.debug:
  logToStdout()

else:
  if not isdir(logdir):
    os.makedirs(logdir)

  if settings.USER:
    print "Dropping privileges to become the user %s" % settings.USER

  from carbon.util import daemonize, dropprivs
  daemonize()

  pf = open(pidfile, 'w')
  pf.write( str(os.getpid()) )
  pf.close()

  def shutdown():
    if os.path.exists(pidfile):
      os.unlink(pidfile)

  atexit.register(shutdown)

  if settings.USER:
    pwent = pwd.getpwnam(settings.USER)
    os.chown(pidfile, pwent.pw_uid, pwent.pw_gid)
    dropprivs(settings.USER)

  logToDir(logdir)

# Configure application components
metricReceived.installHandler(MetricCache.store)
startListener(settings.LINE_RECEIVER_INTERFACE, settings.LINE_RECEIVER_PORT, MetricLineReceiver)
startListener(settings.PICKLE_RECEIVER_INTERFACE, settings.PICKLE_RECEIVER_PORT, MetricPickleReceiver)
startListener(settings.CACHE_QUERY_INTERFACE, settings.CACHE_QUERY_PORT, CacheQueryHandler)

if use_amqp:
  amqp_listener.startReceiver(amqp_host, amqp_port, amqp_user, amqp_password,
                              vhost=amqp_vhost, spec=amqp_spec,
                              exchange_name=amqp_exchange_name,
                              verbose=amqp_verbose)

if settings.ENABLE_MANHOLE:
  from carbon import manhole
  manhole.start()

from carbon.writer import startWriter # have to import this *after* settings are defined
startWriter()
startRecording()


# Run the twisted reactor
print "%s running [instance %s]" % (program, instance)

if options.profile:
  import cProfile

  if exists(options.profile):
    os.unlink(options.profile)

  cProfile.run('reactor.run()', options.profile)

else:
  reactor.run()
