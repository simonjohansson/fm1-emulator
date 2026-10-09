# SPDX-License-Identifier: GPL-2.0-or-later
"""Hardware-qualified source/base alias in stock's pre-index word store."""
from pathlib import Path
import tempfile
import unittest

from support import INSPECTION, Guest, guest_state


class WordPreStoreTests(unittest.TestCase):
    def test_alias_stores_incoming_pointer_at_advanced_address(self):
        with tempfile.TemporaryDirectory(prefix="fm1-pre-store-") as directory:
            guest = Guest()
            guest.write(INSPECTION, 0x5a5a5a5a)
            guest.write(INSPECTION + 4, 0x5a5a5a5a)
            guest.write(INSPECTION + 8, 0x5a5a5a5a)
            guest.literal(0, INSPECTION)
            guest.emit(0xecd0, 0x000b)  # [++r0=8] = r0; measured on FM-1
            state = guest_state(Path(directory), guest)
            self.assertEqual(state["inspection"][:3],
                             [0x5a5a5a5a, 0x5a5a5a5a, INSPECTION])
            self.assertEqual(state["registers"][0], INSPECTION + 8)
            self.assertEqual(state["instructions"], guest.instructions)


if __name__ == "__main__":
    unittest.main()
