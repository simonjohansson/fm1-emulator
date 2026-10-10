#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cross-build the headless emulator for WebAssembly with Emscripten.

    mise run build_wasm

The result is .cache/wasm/dist/qemu-system-pi32v2.{js,wasm}: QEMU's TCG
interpreter (TCI) running the pi32v2 machine. QEMU 11.1's only Emscripten host
is wasm64; its 32-bit address limit lowers the module to 32-bit memory, so it
runs in Chrome, Firefox and Safari. The compilers (emsdk, zig) come from mise; the
libraries QEMU links (zlib, libffi, pixman, GLib) are compiled for WebAssembly
here from pinned sources, as QEMU's tests/docker/dockerfiles/emsdk-wasm64-cross.docker
does. It reuses the native build's pinned QEMU source and Python environment;
the mise task runs that build first.
"""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile

import build
import integrate

DEPENDENCIES = [
    ("zlib", "https://zlib.net/zlib-1.3.2.tar.xz",
     "d7a0654783a4da529d1bb793b7ad9c3318020af77667bcae35f95d0e42a792f3"),
    ("libffi", "https://github.com/libffi/libffi/releases/download/v3.5.2/libffi-3.5.2.tar.gz",
     "f3a3082a23b37c293a4fcd1053147b371f2ff91fa7ea1b2a52e335676bac82dc"),
    ("pixman", "https://cairographics.org/releases/pixman-0.44.2.tar.gz",
     "6349061ce1a338ab6952b92194d1b0377472244208d47ff25bef86fc71973466"),
    ("glib", "https://download.gnome.org/sources/glib/2.84/glib-2.84.0.tar.xz",
     "f8823600cb85425e2815cfad82ea20fdaa538482ab74e7293d58b3f64a5aff6a"),
]
CACHE = build.CACHE
WASM = CACHE / "wasm"
PREFIX = WASM / "prefix"
BUILD = WASM / "build"
DIST = WASM / "dist"
COMPILE = "-O3 -pthread -DWASM_BIGINT -sMEMORY64=1"
LINK = f"-sWASM_BIGINT -sASYNCIFY=1 -sMEMORY64=1 -L{PREFIX / 'lib'}"
# QEMU's configs/meson/emscripten.txt link arguments, plus ENV so the page can
# pass the FM1_POC_* test variables and HEAPU8 so it can read the LCD framebuffer. Meson replaces rather than merges lists, so
# they are restated whole.
QEMU_LINK_ARGS = ["-pthread", "-sASYNCIFY=1", "-sPROXY_TO_PTHREAD=1", "-sFORCE_FILESYSTEM",
                  "-sALLOW_TABLE_GROWTH", "-sTOTAL_MEMORY=2GB", "-sWASM_BIGINT", "-sEXPORT_ES6=1",
                  "-sASYNCIFY_IMPORTS=ffi_call_js",
                  "-sEXPORTED_RUNTIME_METHODS=addFunction,removeFunction,TTY,FS,ENV,HEAPU8"]


def unpack(name, archive):
    target = WASM / "deps-build" / name
    if not target.exists():
        target.mkdir(parents=True)
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                member.name = member.name.split("/", 1)[1] if "/" in member.name else ""
            tar.extractall(target, members=[m for m in tar.getmembers() if m.name], filter="data")
    return target


def write_cross_file():
    def quoted(flags):
        return ", ".join(f"'{flag}'" for flag in shlex.split(flags))
    integrate.write_changed(WASM / "cross.meson",
        "[host_machine]\nsystem = 'emscripten'\ncpu_family = 'wasm64'\ncpu = 'wasm64'\nendian = 'little'\n\n"
        "[binaries]\nc = 'emcc'\ncpp = 'em++'\nar = 'emar'\nranlib = 'emranlib'\n"
        "pkgconfig = ['pkg-config', '--static']\n\n"
        f"[built-in options]\nc_args = [{quoted(COMPILE)}]\ncpp_args = [{quoted(COMPILE)}]\n"
        f"c_link_args = [{quoted(LINK)}]\ncpp_link_args = [{quoted(LINK)}]\n")
    return WASM / "cross.meson"


def build_dependencies(env):
    (WASM / "deps-build").mkdir(parents=True, exist_ok=True)
    cross = write_cross_file()
    sources = {}
    for name, url, checksum in DEPENDENCIES:
        sources[name] = unpack(name, build.fetch(url, Path(url).name, checksum))
    lib = PREFIX / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    meson_common = ["meson", "setup", "_build", f"--prefix={PREFIX}", f"--cross-file={cross}",
                    "--default-library=static", "--buildtype=release"]
    if not (lib / "libz.a").exists():
        # zlib's configure picks macOS libtool as the archiver, which cannot
        # archive WebAssembly objects.
        build.run(["emconfigure", "./configure", f"--prefix={PREFIX}", "--static"],
                  sources["zlib"], env, "wasm-zlib-configure.log")
        build.run(["emmake", "make", "install", "-j8", "AR=emar", "ARFLAGS=rc"],
                  sources["zlib"], env, "wasm-zlib-build.log")
    if not (lib / "libffi.a").exists():
        build.run(["emconfigure", "./configure", "--host=wasm64-unknown-linux", f"--prefix={PREFIX}",
                   "--enable-static", "--disable-shared", "--disable-dependency-tracking",
                   "--disable-builddir", "--disable-multi-os-directory", "--disable-raw-api",
                   "--disable-docs"], sources["libffi"], env, "wasm-libffi-configure.log")
        build.run(["emmake", "make", "install", "SUBDIRS=include", "-j8"],
                  sources["libffi"], env, "wasm-libffi-build.log")
    if not (lib / "libpixman-1.a").exists():
        build.run([*meson_common, "-Dtests=disabled", "-Ddemos=disabled"],
                  sources["pixman"], env, "wasm-pixman-configure.log")
        build.run(["meson", "install", "-C", "_build"], sources["pixman"], env, "wasm-pixman-build.log")
    if not (lib / "libglib-2.0.a").exists():
        # GLib links libresolv for res_query, which Emscripten lacks.
        stub = WASM / "deps-build" / "resolv"
        stub.mkdir(exist_ok=True)
        (stub / "res_query.c").write_text(
            "#include <netdb.h>\nint res_query(const char *n, int c, int t, unsigned char *d, int l)"
            " { h_errno = HOST_NOT_FOUND; return -1; }\n")
        build.run(["emcc", *shlex.split(COMPILE), "-c", "res_query.c", "-fPIC", "-o", "libresolv.o"],
                  stub, env, "wasm-resolv.log")
        build.run(["emar", "rcs", str(lib / "libresolv.a"), "libresolv.o"], stub, env, "wasm-resolv-ar.log")
        build.run([*meson_common, "--force-fallback-for=pcre2", "-Dselinux=disabled", "-Dxattr=false",
                   "-Dlibmount=disabled", "-Dnls=disabled", "-Dtests=false", "-Dglib_debug=disabled",
                   "-Dglib_assert=false", "-Dglib_checks=false"],
                  sources["glib"], env, "wasm-glib-configure.log")
        # Emscripten does not provide these in the final link; meson cannot tell.
        config = sources["glib"] / "_build/config.h"
        config.write_text("".join(
            line for line in config.read_text().splitlines(keepends=True)
            if not line.startswith(("#define HAVE_POSIX_SPAWN 1", "#define HAVE_PTHREAD_GETNAME_NP 1"))))
        build.run(["meson", "install", "-C", "_build"], sources["glib"], env, "wasm-glib-build.log")


def host_compiler():
    """The build machine's clang: the first on PATH outside the Emscripten SDK.

    emsdk ships a clang of its own that cannot link macOS programs, and both are
    on PATH in the mise task, in no fixed order. QEMU's configure ignores CC for
    the build machine, so it is passed explicitly."""
    sdk = Path(shutil.which("emcc")).resolve().parents[2]
    path = os.pathsep.join(entry for entry in os.environ["PATH"].split(os.pathsep)
                           if not Path(entry).resolve().is_relative_to(sdk))
    compiler = shutil.which("clang", path=path)
    if not compiler:
        raise SystemExit("no build-machine clang on PATH: run this as `mise run build_wasm`")
    return compiler


def build_qemu(env, reconfigure):
    source = CACHE / f"qemu-{build.QEMU}"
    if reconfigure:
        # Meson loses the build machine's compiler when this cross build is
        # reconfigured in place, so start again from an empty directory.
        shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    if not (BUILD / "build.ninja").exists():
        build.run(["emconfigure", source / "configure", "--target-list=pi32v2-softmmu",
                   f"--python={CACHE / 'python/bin/python'}", "--without-default-features",
                   "--enable-tcg", "--disable-fdt", "--disable-docs", "--disable-user", "--disable-tools",
                   "--disable-guest-agent", "--disable-slirp", "--disable-capstone",
                   "--static", "--cpu=wasm64", "--enable-tcg-interpreter",
                   # Lower the module to 32-bit memory (Emscripten's MEMORY64=2), which
                   # Safari can run as well; it is no slower than 64-bit memory.
                   "--wasm64-32bit-address-limit",
                   f"--host-cc={host_compiler()}"],
                  BUILD, env, "wasm-configure.log")
    build.run([BUILD / "pyvenv/bin/meson", "configure", ".", "-Dc_link_args=" + " ".join(QEMU_LINK_ARGS)],
              BUILD, env, "wasm-link-args.log")
    build.run(["emmake", "make", "-j8"], BUILD, env, "wasm-build.log")
    DIST.mkdir(exist_ok=True)
    for suffix in (".js", ".wasm"):
        shutil.copy2(BUILD / f"qemu-system-pi32v2{suffix}", DIST / f"qemu-system-pi32v2{suffix}")
    print(f"WebAssembly build: {DIST}")


def main():
    reconfigure = "--reconfigure" in sys.argv
    if not shutil.which("emcc"):
        raise SystemExit("emcc not found: run this as `mise run build_wasm`, which provides the Emscripten SDK")
    source = CACHE / f"qemu-{build.QEMU}"
    venv = CACHE / "python"
    if not (source.exists() and (venv / "bin/meson").exists()):
        raise SystemExit("run this as `mise run build_wasm`: the native build it depends on fetches the pinned QEMU and build tools")
    integrate.main()
    CACHE.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(venv / "bin"), env["PATH"]])
    env["PYTHONNOUSERSITE"] = "1"
    env["CPATH"] = str(PREFIX / "include")
    env["PKG_CONFIG_PATH"] = env["EM_PKG_CONFIG_PATH"] = str(PREFIX / "lib/pkgconfig")
    env["CFLAGS"], env["CXXFLAGS"], env["LDFLAGS"] = COMPILE, COMPILE, LINK
    build_dependencies(env)
    env["CFLAGS"], env["LDFLAGS"] = "-O2 -pthread -DWASM_BIGINT", "-sWASM_BIGINT -sASYNCIFY=1 " + f"-L{PREFIX / 'lib'}"
    build_qemu(env, reconfigure)


if __name__ == "__main__":
    main()
