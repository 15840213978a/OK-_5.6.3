"""Restore the verified 5.6.7 MPV disc-menu binary to the matching Media3 build.

The public MPV binary has libdvdnav/libbluray, but lacks mpv's `discnav` command.
Only libmpv.so is replaced. FFmpeg and the Android libplayer JNI bridge remain
source-build dependencies and are checked against the replacement's ELF symbols.
"""

import argparse
import hashlib
import re
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path


APK_HASHES = {
    "arm64-v8a": {
        "5428c11d5aa09813dfa2fde8a32be2beeeddfc45d093b89e46ed53cd696a3b3c",  # official 5.6.7
        "5cbe3f1b8f4f659bce48e49cb836a56cce91a9ee74432097ba6b78312c9b2bcc",  # disc-dovi-test-37
        "0f1fa3db9267471691115f9d5293f3670d56e2618803830afe6f7aac181acae5",  # disc-dovi-test-41
    },
    "armeabi-v7a": {
        "492bc0b1d4607a41d3c3f56d37bc590be0ee6b8b481bebe52f6dd2b0819d2385",  # official 5.6.7
        "6c28f341a58f832e15edb9858d7428e2a5846fa8658def10e9d177886dada7ce",  # disc-dovi-test-37
        "07d8f45e9a665fb32e75e4de82641c948d7be20cac0933e9e18bb6c23b5fbacf",  # disc-dovi-test-41
    },
}
ELF_MACHINE = {"arm64-v8a": (2, 183), "armeabi-v7a": (1, 40)}
MENU_MARKERS = (b"discnav\0", b"disc-menu-active\0", b"dvdnav_menu_call\0", b"bd_menu_call\0")
FFMPEG_LIBS = (
    "avcodec", "avfilter", "avformat", "avutil", "avdevice", "swresample", "swscale"
)
SYMBOL = re.compile(r"\s*\d+:\s+\S+\s+\d+\s+\w+\s+\w+\s+\w+\s+(\S+)\s+(\S+)")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def symbols(path, *, undefined):
    lines = subprocess.check_output(["readelf", "-W", "--dyn-syms", str(path)], text=True)
    result = set()
    for line in lines.splitlines():
        match = SYMBOL.match(line)
        if match and (match[1] == "UND") == undefined:
            result.add(match[2].replace("@@", "@"))
    return result


def verify_elf(payload, abi):
    elf_class, machine = ELF_MACHINE[abi]
    if (len(payload) < 20 or payload[:4] != b"\x7fELF" or payload[4] != elf_class
            or payload[5] != 1 or struct.unpack_from("<H", payload, 16)[0] != 3
            or struct.unpack_from("<H", payload, 18)[0] != machine):
        raise ValueError(f"Invalid {abi} MPV ELF")
    if not all(marker in payload for marker in MENU_MARKERS):
        raise ValueError(f"Official {abi} MPV does not contain the menu commands")


def verify_linkage(official, source_root, abi):
    dependencies = source_root / "libraries" / "decoder_ffmpeg" / "src" / "main" / "jniLibs" / abi
    bridge = source_root / "libraries" / "mpvplayer" / "src" / "main" / "jniLibs" / abi / "libplayer.so"
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / "libmpv.so"
        binary.write_bytes(official)
        required = symbols(binary, undefined=True)
        exports = set().union(*(symbols(dependencies / f"lib{name}.so", undefined=False)
                                for name in FFMPEG_LIBS))
        missing_ffmpeg = sorted(symbol for symbol in required if symbol.startswith(
            ("av_", "avcodec_", "avfilter_", "avformat_", "avio_", "swr_", "sws_"))
            and "@LIB" in symbol and symbol not in exports)
        if missing_ffmpeg:
            raise ValueError(f"{abi} MPV requires missing FFmpeg symbols: {missing_ffmpeg}")
        mpv_exports = {symbol.split("@")[0] for symbol in symbols(binary, undefined=False)}
        bridge_imports = {symbol.split("@")[0] for symbol in symbols(bridge, undefined=True)
                          if symbol.startswith("mpv_")}
        missing_bridge = sorted(bridge_imports - mpv_exports)
        if missing_bridge:
            raise ValueError(f"{abi} libplayer JNI requires missing MPV symbols: {missing_bridge}")
        print(f"{abi}: MPV menu commands and JNI/FFmpeg symbol linkage verified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm64", type=Path)
    parser.add_argument("--armeabi", type=Path)
    parser.add_argument("--media3-root", type=Path, required=True)
    args = parser.parse_args()
    apks = {abi: path for abi, path in (("arm64-v8a", args.arm64),
                                       ("armeabi-v7a", args.armeabi)) if path is not None}
    if not apks:
        parser.error("Provide at least one verified APK source")

    verified = {}
    for abi, apk in apks.items():
        digest = sha256(apk)
        if digest not in {value.lower() for value in APK_HASHES[abi]}:
            raise ValueError(f"Verified MPV source APK SHA256 mismatch for {abi}: {digest}")
        with zipfile.ZipFile(apk) as archive:
            binary = archive.read(f"lib/{abi}/libmpv.so")  # Also verifies ZIP CRC.
        verify_elf(binary, abi)
        verify_linkage(binary, args.media3_root, abi)
        verified[abi] = binary

    for abi, binary in verified.items():
        target = (args.media3_root / "libraries" / "mpvplayer" / "src" / "main"
                  / "jniLibs" / abi / "libmpv.so")
        target.write_bytes(binary)
        print(f"{abi}: restored {len(binary)} verified bytes to {target}")


if __name__ == "__main__":
    main()
