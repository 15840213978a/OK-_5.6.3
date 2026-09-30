"""Verify the official 5.6.6 Dolby Vision JNI binary against current FFmpeg ABIs."""

import argparse
import re
import subprocess
from pathlib import Path

SYMBOL = re.compile(r"\s*\d+:\s+\S+\s+\d+\s+\w+\s+\w+\s+\w+\s+(\S+)\s+(\S+)")
ABIS = ("arm64-v8a", "armeabi-v7a")


def symbols(path: Path, *, undefined: bool) -> set[str]:
    output = subprocess.check_output(
        ["readelf", "-W", "--dyn-syms", str(path)], text=True
    )
    result: set[str] = set()
    for line in output.splitlines():
        match = SYMBOL.match(line)
        if not match:
            continue
        section, name = match.groups()
        if (section == "UND") != undefined:
            continue
        result.add(name.replace("@@", "@"))
    return result


def needed(path: Path) -> set[str]:
    output = subprocess.check_output(["readelf", "-W", "-d", str(path)], text=True)
    result = set()
    for line in output.splitlines():
        if "(NEEDED)" not in line:
            continue
        start = line.find("[")
        end = line.find("]", start + 1)
        if start >= 0 and end > start:
            result.add(line[start + 1 : end])
    return result


def verify_abi(media3: Path, app: Path, abi: str) -> None:
    dovi = app / "src" / "main" / "jniLibs" / abi / "libffmpegDoviJNI.so"
    avcodec = media3 / "libraries" / "decoder_ffmpeg" / "src" / "main" / "jniLibs" / abi / "libavcodec.so"
    avutil = media3 / "libraries" / "decoder_ffmpeg" / "src" / "main" / "jniLibs" / abi / "libavutil.so"
    for path in (dovi, avcodec, avutil):
        if not path.is_file():
            raise SystemExit(f"Missing ABI input: {path}")

    dependencies = needed(dovi)
    for required in ("libavcodec.so", "libavutil.so"):
        if required not in dependencies:
            raise SystemExit(f"{abi}: {dovi.name} does not declare {required}")

    required_symbols = {
        symbol
        for symbol in symbols(dovi, undefined=True)
        if symbol.startswith("av_") and "@LIBAV" in symbol
    }
    exports = symbols(avcodec, undefined=False) | symbols(avutil, undefined=False)
    if not required_symbols:
        raise SystemExit(f"{abi}: no versioned FFmpeg imports found in official Dolby Vision JNI")
    missing = sorted(required_symbols - exports)
    if missing:
        raise SystemExit(
            f"{abi}: official Dolby Vision JNI requires missing FFmpeg symbols: {missing}"
        )

    required_codec_versions = sorted(
        {symbol.split("@", 1)[1] for symbol in required_symbols if "@LIBAVCODEC_" in symbol}
    )
    required_util_versions = sorted(
        {symbol.split("@", 1)[1] for symbol in required_symbols if "@LIBAVUTIL_" in symbol}
    )
    print(
        f"{abi}: Dolby Vision JNI linkage verified; "
        f"codec={required_codec_versions}, util={required_util_versions}, "
        f"symbols={len(required_symbols)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media3-root", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    args = parser.parse_args()
    for abi in ABIS:
        verify_abi(args.media3_root, args.app_root, abi)


if __name__ == "__main__":
    main()
