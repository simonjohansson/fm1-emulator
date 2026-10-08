# SPDX-License-Identifier: GPL-2.0-or-later
.PHONY: emulator
emulator:
	mise exec python@3.13.15 -- python qemu-poc/build.py --standalone
