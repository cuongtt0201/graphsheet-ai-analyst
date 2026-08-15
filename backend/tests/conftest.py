"""Make `app.*` importable when pytest runs from backend/, without needing the
package installed. Nothing here touches the network or the LLM pool — the whole
suite is deterministic on purpose (see tests/README-ish note in test_harness).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
