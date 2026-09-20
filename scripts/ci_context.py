#!/usr/bin/env python3
"""One predicate for "can a staleness check be enforced in this CI context?"

`scripts/stamp_test_badges.py` learned this rule first: a pull-request CI
build cannot satisfy a check that asserts a generated file matches its
source, because every PR that touches the source makes the generated file
stale, and a PR that restamps it collides with every other PR that restamps
the same line. Three more sites ask the same question about the SW_VERSION
digest — `scripts/stamp_sw_version.py --check`, its pin in
`tests/test_store.py`, and the `css` job's rebuild comparison in
`.github/workflows/test.yml` — and need the same downgrade on the same
trigger. So the predicate lives here once instead of being re-spelled, and
inevitably drifting, at each call site: it had already been copied to
`tests/test_badge_stamp.py`'s skipif before this module existed.

    python scripts/ci_context.py    # exit 0 if enforceable, 1 if advisory-only

`GITHUB_EVENT_NAME` is read **at call time**, not at import, so a caller (or
a test) can set it after this module is loaded and still get the right
answer -- `make test` itself runs under `GITHUB_EVENT_NAME=pull_request` on a
PR build, so any code here that cached the value at import time would bake in
whatever the test runner's own environment happened to be.
"""

import os
import sys


def staleness_is_enforceable() -> bool:
    """False only on a pull-request CI build, where the check cannot be met.

    Deliberately narrow: `GITHUB_EVENT_NAME` is `pull_request` only in that one
    context. A push to main, a local run and a manual dispatch all still
    enforce, so the badge cannot drift anywhere it can actually be fixed.
    """
    return os.environ.get("GITHUB_EVENT_NAME") != "pull_request"


def main():
    if staleness_is_enforceable():
        print("staleness is enforceable in this context")
        return 0
    print("staleness is advisory-only in this context (pull_request build)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
