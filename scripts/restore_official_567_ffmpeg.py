"""Restore the FFmpeg shared-library set from verified official FongMi 5.6.7 APKs.

The official Dolby Vision JNI is linked against FongMi's FFmpeg build. The
official FFmpeg dependency family is staged before the Media3 native build so
CMake links the source-built libffmpegJNI.so against the same 5.6.7 ABI. The
native linker is the compatibility gate; libffmpegJNI.so does not exist yet at
this staging step.
"""

import argparse
import hashlib
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

APK_SHA256 = {
    "arm64-v8a": "5428c11d5aa09813dfa2fde8a32be2beeeddfc45d093b89e46ed53cd696a3b3c",
    "armeabi-v7a": "492bc0b1d4607a41d3c3f56d37bc590be0ee6b8b481bebe52f6dd2b0819d2385",
}

FFMPEG_LIBS = (
    "libavcodec.so",
    "libavdevice.so",
    "libavfilter.so",
    "libavformat.so",
    "libavutil.so",
    "libswresample.so",
    "libswscale.so",
)

EXPECTED_ELF = {
    "arm64-v8a": (2, 183),
    "armeabi-v7a": (1, 40),
}

SYMBOL = re.compile(r"\s*\d+:\s+\S+\s+\d+\s+\w+\s+\w+\s+\w+\s+(\S+)\s+(\S+)")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_elf(data: bytes, abi: str, name: str) -> None:
    elf_class, machine = EXPECTED_ELF[abi]
    if (
        len(data) < 20
        or data[:4] != b"\x7fELF"
        or data[4] != elf_class
        or data[5] != 1
        or struct.unpack_from("<H", data, 16)[0] != 3
        or struct.unpack_from("<H", data, 18)[0] != machine
    ):
        raise ValueError(f"{name} is not a valid shared ELF for {abi}")


def symbols(path: Path, undefined: bool) -> set[str]:
    out = subprocess.check_output(["readelf", "-W", "--dyn-syms", str(path)], text=True)
    result = set()
    for line in out.splitlines():
        match = SYMBOL.match(line)
        if not match:
            continue
        section, name = match.groups()
        if (section == "UND") == undefined:
            result.add(name.replace("@@", "@"))
    return result


def restore(apk: Path, media3: Path, abi: str) -> None:
    digest = sha256(apk)
    if digest != APK_SHA256[abi]:
        raise ValueError(
            f"Official 5.6.7 {abi} APK SHA256 mismatch: expected {APK_SHA256[abi]}, got {digest}"
        )
    target_root = media3 / "libraries/decoder_ffmpeg/src/main/jniLibs" / abi
    target_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(apk) as archive:
        for name in FFMPEG_LIBS:
            entry = f"lib/{abi}/{name}"
            data = archive.read(entry)
            verify_elf(data, abi, name)
            target = target_root / name
            target.write_bytes(data)
            print(f"{abi}: restored {name} ({len(data)} bytes)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm64", type=Path, required=True)
    parser.add_argument("--armeabi", type=Path, required=True)
    parser.add_argument("--media3-root", type=Path, required=True)
    args = parser.parse_args()

    restore(args.arm64, args.media3_root, "arm64-v8a")
    restore(args.armeabi, args.media3_root, "armeabi-v7a")


if __name__ == "__main__":
    main()
