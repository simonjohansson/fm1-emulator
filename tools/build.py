#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the native FM-1 emulator from the pinned QEMU release."""
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import integrate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / ".cache"
QEMU = integrate.QEMU
QEMU_COMMIT = "4fc49f46dc95d4a27de2509e7fceb2931e91faeb"
QEMU_SHA = "731b5681e4bb18be313231579b8efd0296c5b015fa36dc533874b639ba838016"
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


def mise_prefixes(env):
    """The build task's conda-forge environments on PATH, in mise.toml's order.

    mise installs each package into its own environment, with its own copies
    of its dependencies (SDL2's on Linux include GLib), so the first listed
    wins wherever two provide the same library."""
    tools = tomllib.loads((ROOT / "mise.toml").read_text())["tasks"]["build"]["tools"]
    on_path = {Path(entry).parent for entry in env["PATH"].split(os.pathsep)}
    prefixes = []
    for tool in tools:
        if tool.startswith("conda:"):
            prefix = next((p for p in on_path if p.parent.name == "conda-" + tool[6:]), None)
            if prefix is None:
                raise SystemExit(f"{tool} is not on PATH: run this as `mise run build`")
            prefixes.append(prefix)
    return prefixes


def llvm_tools(tools, env):
    """Archive with the LLVM matching mise's clang: the system ar cannot index
    its LTO bitcode, and Meson only accepts an archiver named ar."""
    (tools / "bin").mkdir(parents=True, exist_ok=True)
    for name in ("ar", "ranlib"):
        target = shutil.which(f"llvm-{name}", path=env["PATH"])
        if not target:
            raise SystemExit(f"no llvm-{name} on PATH: run this as `mise run build`")
        target = Path(target)
        link = tools / "bin" / name
        if not link.is_symlink() or link.readlink() != target:
            link.unlink(missing_ok=True)
            link.symlink_to(target)


def bundle_libraries(executable, lib, prefixes):
    """Copy the mise-provided shared libraries the executable loads into lib/."""
    found = {}
    if sys.platform == "darwin":
        pending = [executable]
        while pending:
            listing = subprocess.check_output(["otool", "-L", str(pending.pop())], text=True)
            for line in listing.splitlines()[1:]:
                name = line.strip().split(" (", 1)[0]
                if name.startswith(("/usr/lib/", "/System/Library/")):
                    continue
                if not name.startswith("@rpath/"):
                    raise SystemExit(f"unexpected library dependency: {name}")
                name = name.removeprefix("@rpath/")
                if name not in found:
                    found[name] = next((prefix / "lib" / name for prefix in prefixes
                                        if (prefix / "lib" / name).is_file()), None)
                    if found[name] is None:
                        raise SystemExit(f"no mise package provides {name}")
                    pending.append(found[name])
    else:
        env = dict(os.environ, LD_LIBRARY_PATH=os.pathsep.join(str(p / "lib") for p in prefixes))
        for line in subprocess.check_output(["ldd", str(executable)], env=env, text=True).splitlines():
            name, _, path = line.strip().partition(" => ")
            path = Path(path.split(" (", 1)[0])
            if any(path.is_relative_to(prefix) for prefix in prefixes):
                found[name] = path
            elif "not found" in line:
                raise SystemExit(f"no mise package provides {name}")
    shutil.rmtree(lib, ignore_errors=True)
    lib.mkdir()
    for name, path in found.items():
        shutil.copy2(path, lib / name)


def publish_executable(build, source, prefixes):
    """Publish the executable with its libraries and reject any it cannot find."""
    release = CACHE / "release"
    release.mkdir(exist_ok=True)
    target = release / "emulator.new"
    shutil.copy2(build / "qemu-system-pi32v2", target)
    bundle_libraries(target, release / "lib", prefixes)
    if sys.platform == "darwin":
        subprocess.run(["codesign", "--force", "--sign", "-", str(target)], check=True)
        subprocess.run(["codesign", "--verify", "--strict", str(target)], check=True)
    # The executable must load from lib/ alone, without the mise packages.
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("DYLD_", "LD_"))}
    if subprocess.run([str(target), "--version"], env=clean, capture_output=True).returncode:
        target.unlink()
        raise SystemExit("the published executable does not start with its bundled libraries")
    target.replace(release / "emulator")
    # A link, so the executable finds lib/ next to its real path. Replacing it
    # is atomic, preserving a running executable and the last good build.
    with tempfile.TemporaryDirectory(prefix=".emulator-", dir=HERE.parent) as staged:
        executable = Path(staged) / "emulator"
        executable.symlink_to(release / "emulator")
        executable.replace(HERE.parent / "emulator")
    shutil.copy2(ROOT / "LICENSE", release / "COPYING.GPL-2.0")
    shutil.copy2(ROOT / "LICENSES.md", release / "LICENSES.md")
    # Retain the notices of the bundled libraries; QEMU ships the LGPL text.
    notices = {"GLib-LGPL-2.1.txt": source / "COPYING.LIB"}
    notices |= {"PCRE2-COPYING.txt": prefix / "share/doc/pcre2/COPYING" for prefix in prefixes
                if (prefix / "share/doc/pcre2/COPYING").exists()}
    for name, notice in notices.items():
        if notice.exists():
            shutil.copy2(notice, release / name)
    integrate.write_changed(release / "RUNNING.txt",
        "Run: ./emulator path/to/firmware.bin\n"
        "Close the window to quit. Use --help for controls.\n"
        "The USB console writes to stdout and accepts commands from stdin.\n"
        "Python and third-party library installations are not required to run.\n"
        "Raw .bin and uncompressed .fwsc/.ufw loading is supported; firmware compatibility varies.\n"
        "For redistribution retain licensing and provide the corresponding source/build inputs.\n")
    print(f"Standalone executable: {HERE.parent / 'emulator'}")
    system = "macos" if sys.platform == "darwin" else sys.platform
    package = CACHE / f"fm1-emulator-{system}-{platform.machine()}.tar.gz"
    with tarfile.open(package, "w:gz") as archive:
        for item in sorted(release.iterdir()):
            archive.add(item, arcname=item.name)
    print(f"Distribution archive: {package}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reconfigure", action="store_true")
    parser.add_argument("--standalone", action="store_true",
                        help="publish ./emulator and an archive with its libraries bundled")
    args = parser.parse_args()
    build = CACHE / ("build-standalone" if args.standalone else "build")
    CACHE.mkdir(exist_ok=True)
    # An older Ninja build remains tied to its original source tree. Do not
    # rebuild it and report the new pin as though an upgrade occurred.
    project_info = build / "meson-info/intro-projectinfo.json"
    if project_info.exists() and json.loads(project_info.read_text())["version"] != QEMU:
        raise SystemExit(f"cached build belongs to another QEMU release; preserve "
                         f"{build} elsewhere and rebuild QEMU {QEMU}")
    env = dict(os.environ)
    env["PIP_CACHE_DIR"] = str(CACHE / "pip-cache")
    env["PYTHONNOUSERSITE"] = "1"
    # This interpreter must have been selected with mise (see README).
    if sys.version_info[:2] != (3, 13):
        raise SystemExit("run with: mise run build")
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
    prefixes = mise_prefixes(env)
    env["PKG_CONFIG_PATH"] = os.pathsep.join(str(prefix / "lib/pkgconfig") for prefix in prefixes)
    llvm_tools(tools, env)
    integrate.main()
    build.mkdir(exist_ok=True)
    config_host = build / "config-host.h"
    needs_sdl = (not config_host.exists() or
                 "#define CONFIG_SDL" not in config_host.read_text().splitlines())
    build_options = build / "meson-info/intro-buildoptions.json"
    lto_enabled = build_options.exists() and any(
        option["name"] == "b_lto" and option["value"] is True
        for option in json.loads(build_options.read_text()))
    if args.reconfigure or not (build / "build.ninja").exists() or needs_sdl or not lto_enabled:
        # GNU ld cannot link LLVM bitcode; macOS's linker can.
        ldflags = "-Wl,-rpath,@executable_path/lib" if sys.platform == "darwin" else \
            "-fuse-ld=lld -Wl,-rpath,$ORIGIN/lib"
        # SDL_syswm.h includes Xlib.h, whose protocol headers are in xorgproto.
        xorgproto = next(p for p in prefixes if p.parent.name == "conda-xorg-xorgproto")
        cflags = "" if sys.platform == "darwin" else f"-isystem {xorgproto / 'include'}"
        run([source / "configure", "--target-list=pi32v2-softmmu", f"--python={venv / 'bin/python'}",
             "--cc=clang", "--objcc=clang", "--host-cc=clang",
             "--without-default-features", "--enable-tcg", "--disable-fdt", "--disable-docs",
             "--disable-user", "--disable-tools", "--disable-guest-agent", "--disable-slirp",
             "--disable-capstone", "--enable-werror",
             "--enable-sdl", "--audio-drv-list=sdl", "--enable-lto",
             f"--extra-cflags={cflags}", f"--extra-ldflags={ldflags}"],
            build, env, "configure.log")
    run([venv / "bin/ninja", "-j8", "qemu-system-pi32v2"], build, env, "build.log")
    print(f"Built {build / 'qemu-system-pi32v2'} (QEMU {QEMU}, {QEMU_COMMIT})")
    if args.standalone:
        publish_executable(build, source, prefixes)


if __name__ == "__main__":
    main()
