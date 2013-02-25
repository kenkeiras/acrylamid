
import attest
tt = tt = attest.Tests()

import re
from acrylamid import assets


def uses(patterns, cls):
    for item in patterns:
        assert re.match(cls.uses, item) is not None


@tt.test
def less():

    assert re.match(assets.LESS.uses, '@foo "bar.less";') is None

    patterns = [
        '@import "lib.less";',
        '@import-once "lib.less";',

        '@import "lib";',
        '@import "lib.css";',
        '@import-once "lib.less"; // some comment',
    ]

    uses(patterns, assets.LESS)


@tt.test
def scss():

    assert re.match(assets.SCSS.uses, '@foo "bar.scss";') is None

    patterns = [
        '@import "lib";',
        '@import "lib.scss";',
        '@import "lib.css";'
    ]

    uses(patterns, assets.SCSS)
