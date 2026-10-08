#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build only this experimental target; all writable caches stay beside it."""
import argparse
import hashlib
import json
import os
import platform
import shlex
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
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


def static_glib(env):
    """Select third-party archives without statically linking macOS itself."""
    metadata = CACHE / "standalone-pkgconfig"
    env["PKG_CONFIG_PATH"] = os.pathsep.join(
        part for part in env.get("PKG_CONFIG_PATH", "").split(os.pathsep)
        if part != str(metadata))
    pkgconfig = shutil.which("pkg-config", path=env["PATH"])
    def query(*args):
        return subprocess.check_output([pkgconfig, *args, "glib-2.0"],
                                       env=env, text=True).strip()
    libs = shlex.split(query("--libs", "--static"))
    directories = [Path(flag[2:]) for flag in libs if flag.startswith("-L")]
    for index, flag in enumerate(libs):
        if flag in ("-lglib-2.0", "-lintl", "-lpcre2-8"):
            archive = next((directory / ("lib" + flag[2:] + ".a")
                            for directory in directories
                            if (directory / ("lib" + flag[2:] + ".a")).is_file()), None)
            if archive is None:
                raise SystemExit(f"standalone build needs the static archive for {flag}")
            libs[index] = str(archive)
    if "-lglib-2.0" not in shlex.split(query("--libs")):
        raise SystemExit("unexpected GLib package metadata")
    metadata.mkdir(exist_ok=True)
    package = metadata / "glib-2.0.pc"
    previous = package.read_text() if package.exists() else None
    integrate.write_changed(package,
        "Name: GLib (FM-1 standalone)\nDescription: Statically linked GLib\n"
        f"Version: {query('--modversion')}\nLibs: {shlex.join(libs)}\n"
        f"Cflags: {query('--cflags')}\n")
    env["PKG_CONFIG_PATH"] = os.pathsep.join([str(metadata), env.get("PKG_CONFIG_PATH", "")])
    return previous != package.read_text()


def publish_executable(build):
    """Publish one native executable and reject installed-library dependencies."""
    release = CACHE / "release"
    release.mkdir(exist_ok=True)
    target = release / "emulator.new"
    shutil.copy2(build / "qemu-system-pi32v2", target)
    linked = subprocess.check_output(["otool", "-L", str(target)], text=True)
    for line in linked.splitlines()[1:]:
        dependency = line.strip().split(" (", 1)[0]
        if not dependency.startswith(("/usr/lib/", "/System/Library/")):
            target.unlink()
            raise SystemExit(f"standalone build still needs an installed library: {dependency}")
    subprocess.run(["codesign", "--force", "--sign", "-", str(target)], check=True)
    subprocess.run(["codesign", "--verify", "--strict", str(target)], check=True)
    target.replace(release / "emulator")
    # Publish atomically, preserving a running executable and the last good build.
    with tempfile.TemporaryDirectory(prefix=".emulator-", dir=HERE.parent) as staged:
        executable = Path(staged) / "emulator"
        shutil.copy2(release / "emulator", executable)
        executable.replace(HERE.parent / "emulator")
    shutil.copy2(HERE / "COPYING.GPL-2.0", release / "COPYING.GPL-2.0")
    shutil.copy2(HERE / "LICENSES.md", release / "LICENSES.md")
    # Retain the notices supplied with the statically linked dependencies.
    notices = {
        "GLib-LGPL-2.1.txt": Path("/opt/homebrew/opt/glib/LGPL-2.1-or-later.txt"),
        "PCRE2-COPYING.txt": Path("/opt/homebrew/opt/pcre2/COPYING"),
    }
    for name, source in notices.items():
        if source.exists():
            shutil.copy2(source, release / name)
    integrate.write_changed(release / "RUNNING.txt",
        "Run: ./emulator path/to/firmware.bin\n"
        "Close the window to quit. Use --help for controls.\n"
        "The USB console writes to stdout and accepts commands from stdin.\n"
        "Python and third-party library installations are not required to run.\n"
        "This executable targets application-entry binaries; ROM/package boot is unsupported.\n"
        "For redistribution retain licensing and provide the corresponding source/build inputs.\n")
    print(f"Standalone executable: {HERE.parent / 'emulator'}")
    package = CACHE / f"fm1-emulator-macos-{platform.machine()}.tar.gz"
    with tarfile.open(package, "w:gz") as archive:
        for item in sorted(release.iterdir()):
            archive.add(item, arcname=item.name)
    print(f"Distribution archive: {package}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reconfigure", action="store_true")
    parser.add_argument("--standalone", action="store_true",
                        help="build a native macOS executable with third-party libraries embedded")
    args = parser.parse_args()
    if args.standalone and sys.platform != "darwin":
        raise SystemExit("standalone packaging currently targets macOS")
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
    needs_static_deps = args.standalone and static_glib(env)
    integrate.main()
    build.mkdir(exist_ok=True)
    cocoa = sys.platform == "darwin"
    config_host = build / "config-host.h"
    needs_cocoa = cocoa and (not config_host.exists() or
                             "#define CONFIG_COCOA" not in config_host.read_text().splitlines())
    needs_audio = cocoa and (not config_host.exists() or
                            "#define CONFIG_AUDIO_COREAUDIO" not in config_host.read_text().splitlines())
    build_options = build / "meson-info/intro-buildoptions.json"
    lto_enabled = build_options.exists() and any(
        option["name"] == "b_lto" and option["value"] is True
        for option in json.loads(build_options.read_text()))
    needs_lto = cocoa and not lto_enabled
    if args.reconfigure or not (build / "build.ninja").exists() or needs_cocoa or needs_lto or needs_audio or needs_static_deps:
        run([source / "configure", "--target-list=pi32v2-softmmu", f"--python={venv / 'bin/python'}",
             "--without-default-features", "--enable-tcg", "--disable-fdt", "--disable-docs",
             "--disable-user", "--disable-tools", "--disable-guest-agent", "--disable-slirp",
             "--disable-capstone", "--enable-werror",
             *(["--enable-cocoa", "--enable-coreaudio", "--enable-lto"] if cocoa else [])],
            build, env, "configure.log")
    run([venv / "bin/ninja", "-j8", "qemu-system-pi32v2"], build, env, "build.log")
    print(f"Built {build / 'qemu-system-pi32v2'} (QEMU {QEMU}, {QEMU_COMMIT})")
    if args.standalone:
        publish_executable(build)


if __name__ == "__main__":
    main()
