#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cross-build the headless emulator for WebAssembly with Emscripten.

    mise run build_wasm

The result is .cache/wasm/dist/qemu-system-pi32v2.{js,wasm}: QEMU's TCG
interpreter (TCI) running the pi32v2 machine in a 64-bit WebAssembly module
(QEMU 11.1's only Emscripten host is wasm64, so it needs a browser with
memory64: Chrome, Firefox). The compilers (emsdk, zig) come from mise; the
libraries QEMU links (zlib, libffi, pixman, GLib) are compiled for WebAssembly
here from pinned sources, as QEMU's tests/docker/dockerfiles/emsdk-wasm64-cross.docker
does. The native `mise run build` must have run once: this reuses its pinned
QEMU source, Python environment and pkgconf.
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
# pass the FM1_POC_* test variables. Meson replaces rather than merges lists, so
# they are restated whole.
QEMU_LINK_ARGS = ["-pthread", "-sASYNCIFY=1", "-sPROXY_TO_PTHREAD=1", "-sFORCE_FILESYSTEM",
                  "-sALLOW_TABLE_GROWTH", "-sTOTAL_MEMORY=2GB", "-sWASM_BIGINT", "-sEXPORT_ES6=1",
                  "-sASYNCIFY_IMPORTS=ffi_call_js",
                  "-sEXPORTED_RUNTIME_METHODS=addFunction,removeFunction,TTY,FS,ENV"]


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


def build_qemu(env, reconfigure):
    source = CACHE / f"qemu-{build.QEMU}"
    BUILD.mkdir(parents=True, exist_ok=True)
    if reconfigure or not (BUILD / "build.ninja").exists():
        build.run(["emconfigure", source / "configure", "--target-list=pi32v2-softmmu",
                   f"--python={CACHE / 'python/bin/python'}", "--without-default-features",
                   "--enable-tcg", "--disable-fdt", "--disable-docs", "--disable-user", "--disable-tools",
                   "--disable-guest-agent", "--disable-slirp", "--disable-capstone",
                   "--static", "--cpu=wasm64", "--enable-tcg-interpreter",
                   # configure ignores CC for the build machine and would use cc.
                   f"--host-cc={env.get('CC', 'cc')}"],
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
    venv, tools = CACHE / "python", CACHE / "tools"
    if not (source.exists() and (venv / "bin/meson").exists() and (tools / "bin/pkgconf").exists()):
        raise SystemExit("run `mise run build` once first: it fetches the pinned QEMU and build tools")
    integrate.main()
    CACHE.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(venv / "bin"), str(tools / "bin"), env["PATH"]])
    env["PYTHONNOUSERSITE"] = "1"
    env["CPATH"] = str(PREFIX / "include")
    env["PKG_CONFIG_PATH"] = env["EM_PKG_CONFIG_PATH"] = str(PREFIX / "lib/pkgconfig")
    env["CFLAGS"], env["CXXFLAGS"], env["LDFLAGS"] = COMPILE, COMPILE, LINK
    build_dependencies(env)
    env["CFLAGS"], env["LDFLAGS"] = "-O2 -pthread -DWASM_BIGINT", "-sWASM_BIGINT -sASYNCIFY=1 " + f"-L{PREFIX / 'lib'}"
    build_qemu(env, reconfigure)


if __name__ == "__main__":
    main()
