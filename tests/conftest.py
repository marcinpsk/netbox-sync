# -*- coding: utf-8 -*-
#  Copyright (c) 2020 - 2026 Ricardo Bartels. All rights reserved.
#
#  netbox-sync.py
#
#  This work is licensed under the terms of the MIT license.
#  For a copy, see file LICENSE.txt included in this
#  repository or visit: <https://opensource.org/licenses/MIT>.

"""
Local pytest bootstrap.

Kept self-contained in the tests/ directory on purpose so the upstream pyproject.toml /
uv.lock stay untouched - the test harness (pytest dependency, ini options) is meant to be
wired up on our fork, not in the upstream project. Running the tests only needs pytest, e.g.:

    uv run --native-tls --with pytest pytest tests/
"""

import os
import sys

# make the project importable (the `module` package lives at the repo root)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
