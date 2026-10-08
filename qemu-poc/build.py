#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build only this experimental target; all writable caches stay beside it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import integrate

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
QEMU = integrate.QEMU
QEMU_COMMIT = "4fc49f46dc95d4a27de2509e7fceb2931e91faeb"
QEMU_SHA = "731b5681e4bb18be313231579b8efd0296c5b015fa36dc533874b639ba838016"
PKGCONF_SHA = "3a9080ac51d03615e7c1910a0a2a8df08424892b5f13b0628a204d3fcce0ea8b"
PACKAGES = ["meson==1.5.0", "ninja==1.11.1.4", "distlib==0.3.9", "pycotap==1.3.1"]


def fetch(url, filename, checksum):
    target = CACHE / filename
    if not target.exists():
        print(f"Downloading {filename}", flush=True)
        partial = target.with_suffix(target.suffix + ".part")
        urllib.request.urlretrieve(url, partial)
        partial.replace(target)
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != checksum:
        raise SystemExit(f"checksum mismatch: {filename}: {actual}")
    return target


def run(command, cwd, env, log):
    print(f"{log}", flush=True)
    with (CACHE / log).open("w") as out:
        result = subprocess.run(list(map(str, command)), cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT)
    if result.returncode:
        lines = (CACHE / log).read_text(errors="replace").splitlines()
        print("\n".join(lines[-35:]), file=sys.stderr)
        raise SystemExit(f"build step failed; inspect {CACHE / log}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reconfigure", action="store_true")
    args = parser.parse_args()
    CACHE.mkdir(exist_ok=True)
    # An older Ninja build remains tied to its original source tree. Do not
    # rebuild it and report the new pin as though an upgrade occurred.
    project_info = CACHE / "build/meson-info/intro-projectinfo.json"
    if project_info.exists() and json.loads(project_info.read_text())["version"] != QEMU:
        raise SystemExit(f"cached build belongs to another QEMU release; preserve "
                         f"{CACHE / 'build'} elsewhere and rebuild QEMU {QEMU}")
    env = dict(os.environ)
    env["PIP_CACHE_DIR"] = str(CACHE / "pip-cache")
    env["PYTHONNOUSERSITE"] = "1"
    # This interpreter must have been selected with mise (see README).
    if sys.version_info[:2] != (3, 13):
        raise SystemExit("run with: mise exec python@3.13.15 -- python qemu-poc/build.py")
    archive = fetch(f"https://download.qemu.org/qemu-{QEMU}.tar.xz", f"qemu-{QEMU}.tar.xz", QEMU_SHA)
    source = CACHE / f"qemu-{QEMU}"
    if not source.exists():
        with tarfile.open(archive) as tar:
            # EDK2 is unused by this target; its upstream X11 include link
            # points outside the archive and is rejected by the data filter.
            excluded = f"qemu-{QEMU}/roms/edk2/EmulatorPkg/Unix/Host/X11IncludeHack"
            members = [member for member in tar if member.name != excluded]
            tar.extractall(CACHE, members=members, filter="data")
    venv = CACHE / "python"
    if not venv.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    if not (venv / ".requirements").exists() or (venv / ".requirements").read_text() != "\n".join(PACKAGES):
        run([venv / "bin/pip", "install", *PACKAGES], HERE, env, "python-dependencies.log")
        (venv / ".requirements").write_text("\n".join(PACKAGES))
    tools = CACHE / "tools"
    env["PATH"] = os.pathsep.join([str(venv / "bin"), str(tools / "bin"), env["PATH"]])
    if not shutil.which("pkg-config", path=env["PATH"]):
        archive = fetch("https://distfiles.dereferenced.org/pkgconf/pkgconf-2.3.0.tar.xz", "pkgconf-2.3.0.tar.xz", PKGCONF_SHA)
        pkgsource = CACHE / "pkgconf-2.3.0"
        if not pkgsource.exists():
            with tarfile.open(archive) as tar:
                tar.extractall(CACHE, filter="data")
        run([pkgsource / "configure", f"--prefix={tools}", "--disable-shared"], pkgsource, env, "pkgconf-configure.log")
        run(["make", "-j8"], pkgsource, env, "pkgconf-build.log")
        run(["make", "install"], pkgsource, env, "pkgconf-install.log")
        (tools / "bin/pkg-config").symlink_to("pkgconf")
    if sys.platform == "darwin":
        pc_dirs = sorted(Path("/opt/homebrew/opt").glob("*/lib/pkgconfig"))
        env["PKG_CONFIG_PATH"] = os.pathsep.join([env.get("PKG_CONFIG_PATH", ""), *map(str, pc_dirs)])
    integrate.main()
    build = CACHE / "build"
    build.mkdir(exist_ok=True)
    cocoa = sys.platform == "darwin"
    config_host = build / "config-host.h"
    needs_cocoa = cocoa and (not config_host.exists() or
                             "#define CONFIG_COCOA" not in config_host.read_text().splitlines())
    build_options = build / "meson-info/intro-buildoptions.json"
    lto_enabled = build_options.exists() and any(
        option["name"] == "b_lto" and option["value"] is True
        for option in json.loads(build_options.read_text()))
    needs_lto = cocoa and not lto_enabled
    if args.reconfigure or not (build / "build.ninja").exists() or needs_cocoa or needs_lto:
        run([source / "configure", "--target-list=pi32v2-softmmu", f"--python={venv / 'bin/python'}",
             "--without-default-features", "--enable-tcg", "--disable-fdt", "--disable-docs",
             "--disable-user", "--disable-tools", "--disable-guest-agent", "--disable-slirp",
             "--disable-capstone", "--enable-werror",
             *(["--enable-cocoa", "--enable-lto"] if cocoa else [])],
            build, env, "configure.log")
    run([venv / "bin/ninja", "-j8", "qemu-system-pi32v2"], build, env, "build.log")
    print(f"Built {build / 'qemu-system-pi32v2'} (QEMU {QEMU}, {QEMU_COMMIT})")


if __name__ == "__main__":
    main()
