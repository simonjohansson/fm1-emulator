# SPDX-License-Identifier: GPL-2.0-or-later
.PHONY: all emulator test

all: emulator

emulator:
	mise exec python@3.13.15 -- python tools/build.py --standalone

test:
	mise exec python@3.13.15 -- python -m unittest discover -s tests -p 'test_*.py' -v
