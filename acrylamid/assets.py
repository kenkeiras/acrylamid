# -*- encoding: utf-8 -*-
#
# Copyright 2013 Martin Zimmermann <info@posativ.org>. All rights reserved.
# License: BSD Style, 2 clauses -- see LICENSE.

import os
import io
import re
import time
import stat

from tempfile import mkstemp
from functools import partial
from itertools import chain
from collections import defaultdict
from os.path import join, isfile, getmtime, split, splitext, dirname

from acrylamid import core, helpers, log
from acrylamid.errors import AcrylamidException
from acrylamid.helpers import mkfile, event
from acrylamid.readers import relfilelist

ns = "assets"

__writers = None
__defaultwriter = None


class Writer(object):
    """A 'open-file-and-write-to-dest' writer.  Only updates the destination
    if the source has been a newer modication timestamp or the destination does
    not yet exist.

    .. attribute:: uses

        You can define a `uses` regular expression to automagically omit
        imports/includes within a master file from the output.  Acrylamid will
        also check for the modification timestamps of these referenced item
        and trigger recompilation for the master file.

        Note, that Acrylamid only supports imports/includes up to a depth of six.
    """

    uses = None

    def __init__(self, conf, env):
        self.conf = conf
        self.env = env

    def filter(self, input, directory):
        # remove referenced items from any file from input
        return input - set(chain(*chain(map(partial(self.includesfor, directory), input))))

    def includesfor(self, directory, path):

        if not self.uses:
            return set()

        with io.open(join(directory, path)) as fp:
            return set(join(dirname(path), m.group('file')) for m in
                re.finditer(self.uses, fp.read(512), re.MULTILINE))

    def modified(self, (directory, path), dest):

        mtime = -1
        maxdepth = 6

        # shortcut to getmtime for directory
        f = lambda p: getmtime(join(directory, p))

        # start with the actual file
        includes = [path]

        while maxdepth > 0:

            includes = sum((list(self.includesfor(directory, p)) for p in includes), [])
            mtime = max(max(map(f, chain(includes, [path]))), mtime)

            maxdepth -= 1

        return not isfile(dest) or mtime > getmtime(dest)

    def generate(self, src, dest):
        return io.open(src, 'rb')

    def write(self, (directory, path), dest, force=False, dryrun=False):
        if not force and not self.modified((directory, path), dest):
            return event.skip(ns, dest)

        src = join(directory, path)
        mkfile(self.generate(src, dest), dest, ns=ns, force=force, dryrun=dryrun)

    def shutdown(self):
        pass


class HTML(Writer):
    """Copy HTML files to output if not in theme directory."""

    ext = '.html'

    def write(self, (directory, path), dest, force=False, dryrun=False):

        if directory == self.conf['theme']:
            return

        return super(HTML, self).write((directory, path), dest, force, dryrun)


class XML(HTML):

    ext = '.xml'


class Template(HTML):
    """Transform HTML files using the current markup engine. You can inherit
    from all theme files inside the theme directory."""

    def __init__(self, conf, env):
        env.engine.extend(conf['static'])
        super(Template, self).__init__(conf, env)

    def generate(self, src, dest):
        relpath = split(src[::-1])[0][::-1]  # (head, tail) but reversed behavior
        return self.env.engine.fromfile(relpath).render(env=self.env, conf=self.conf)

    def write(self, (directory, path), dest, force=False, dryrun=False):
        dest = dest.replace(splitext(path)[-1], self.target)
        return super(Template, self).write((directory, path), dest, force, dryrun)

    @property
    def ext(self):
        return self.env.engine.extension

    target = '.html'


class System(Writer):

    def write(self, (directory, path), dest, force=False, dryrun=False):

        src = join(directory, path)
        dest = dest.replace(splitext(src)[-1], self.target)

        if not force and not self.modified((directory, path), dest):
            return event.skip(ns, dest)

        if isinstance(self.cmd, basestring):
            self.cmd = [self.cmd, ]

        tt = time.time()
        fd, path = mkstemp(dir=core.cache.cache_dir)

        # make destination group/world-readable as other files from Acrylamid
        os.chmod(path, os.stat(path).st_mode | stat.S_IRGRP | stat.S_IROTH)

        try:
            res = helpers.system(self.cmd + [src])
        except (OSError, AcrylamidException) as e:
            if isfile(dest):
                os.unlink(dest)
            log.exception('%s: %s' % (e.__class__.__name__, e.args[0]))
        else:
            with os.fdopen(fd, 'w') as fp:
                fp.write(res)

            with io.open(path, 'rb') as fp:
                mkfile(fp, dest, time.time()-tt, ns, force, dryrun)
        finally:
            os.unlink(path)


class SASS(System):

    ext, target = '.sass', '.css'
    cmd = ['sass', ]

    # matches @import 'foo.sass' (and optionally without quotes)
    uses = r'^@import ["\']?(?P<file>.+?\.sass)["\']?'


class SCSS(System):

    ext, target = '.scss', '.css'
    cmd = ['sass', '--scss']

    # matches @import 'foo.scss' / 'foo', we do not support import url(foo);
    uses = r'^@import ["\'](?P<file>.+?(\.scss)?)["\'];'


class LESS(System):

    ext, target = '.less', '.css'
    cmd = ['lessc', ]

    # matches @import 'foo.less'; and @import-once ...
    uses = r'^@import(-once)? ["\'](?P<file>.+?(\.(less|css))?)["\'];'


class CoffeeScript(System):

    ext, target = '.coffee', '.js'
    cmd = ['coffee', '-cp']


class IcedCoffeeScript(System):

    ext, target = ['.iced', '.coffee'], '.js'
    cmd = ['iced', '-cp']


def worker(conf, env, args):
    """Compile each file extension for each folder in its own process.
    """
    (ext, directory), items = args[0], args[1]
    writer = __writers.get(ext, __defaultwriter)

    for path in writer.filter(items, directory):
        writer.write((directory, path), join(conf['output_dir'], path),
            force=env.options.force, dryrun=env.options.dryrun)
    writer.shutdown()


def compile(conf, env):
    """Copy/Compile assets to output directory.  All assets from the theme
    directory (except for templates) and static directories can be compiled or
    just copied using several built-in writers."""

    global __writers, __defaultwriter
    __writers = {}
    __defaultwriter = Writer(conf, env)

    files = defaultdict(set)
    for cls in [globals()[writer](conf, env) for writer in conf.static_filter]:
        if isinstance(cls.ext, list):
            for ext in cls.ext:
                __writers[ext] = cls
        else:
            __writers[cls.ext] = cls

    theme = relfilelist(conf['theme'], conf['theme_ignore'], env.engine.templates)
    static = relfilelist(conf['static'], conf['static_ignore'])

    for path, directory in chain(theme, static):
        files[(splitext(path)[1], directory)].add(path)

    map(partial(worker, conf, env), files.iteritems())
