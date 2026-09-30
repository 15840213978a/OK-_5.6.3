"""Verify staged FongMi 5.6.6 native bridges against the source-built FFmpeg ABI."""

import argparse
import re
import subprocess
from pathlib import Path

FFMPEG_LIBS = ("avcodec", "avutil")
ABIS = ("arm64-v8a", "armeabi-v7a")
SYMBOL = re.compile(r"\s*\d+:\s+\S+\s+\d+\s+\w+\s+\w+\s+\w+\s+(\S+)\s+(\S+)")


def symbols(path: Path, undefined: bool) -> set[str]:
    output = subprocess.check_output(["readelf", "-W", "--dyn-syms", str(path)], text=True)
    result = set()
    for line in output.splitlines():
        match = SYMBOL.match(line)
        if match and (match[1] == "UND") == undefined:
            result.add(match[2].replace("@@", "@"))
    return result


def needed(path: Path) -> set[str]:
    output = subprocess.check_output(["readelf", "-d", str(path)], text=True)
    result = set()
    for line in output.splitlines():
        if "(NEEDED)" not in line:
            continue
        start = line.find("[")
        end = line.find("]", start + 1)
        if start >= 0 and end > start:
            result.add(line[start + 1 : end])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media3-root", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    args = parser.parse_args()

    for abi in ABIS:
        dovi = args.app_root / "src/main/jniLibs" / abi / "libffmpegDoviJNI.so"
        iso = args.app_root / "src/main/jniLibs" / abi / "libisoJNI.so"
        if not dovi.is_file() or not iso.is_file():
            raise SystemExit(f"missing staged native bridge for {abi}")

        dovi_needed = needed(dovi)
        expected_needed = {"libavcodec.so", "libavutil.so"}
        if not expected_needed.issubset(dovi_needed):
            raise SystemExit(f"{abi}: unexpected Dolby Vision dependencies: {sorted(dovi_needed)}")

        required = {
            symbol
            for symbol in symbols(dovi, undefined=True)
            if symbol.startswith(("av_", "avcodec_", "avutil_"))
        }
        exports = set()
        for lib in FFMPEG_LIBS:
            path = (
                args.media3_root
                / "libraries/decoder_ffmpeg/src/main/jniLibs"
                / abi
                / f"lib{lib}.so"
            )
            if not path.is_file():
                raise SystemExit(f"{abi}: missing source-built {path.name}")
            exports.update(symbols(path, undefined=False))

        missing = sorted(symbol for symbol in required if symbol not in exports)
        if missing:
            raise SystemExit(
                f"{abi}: official libffmpegDoviJNI.so requires missing FFmpeg symbols: {missing}"
            )

        iso_needed = needed(iso)
        forbidden = {"libavcodec.so", "libavutil.so"} & iso_needed
        if forbidden:
            raise SystemExit(f"{abi}: unexpected FFmpeg dependency in libisoJNI.so: {sorted(forbidden)}")

        required_rpu = {
            "av_dovi_find_level@LIBAVUTIL_61",
            "av_dovi_rpu_parser_alloc@LIBAVCODEC_63",
            "av_dovi_rpu_parser_free@LIBAVCODEC_63",
            "av_dovi_rpu_parser_parse@LIBAVCODEC_63",
            "av_dovi_rpu_parser_flush@LIBAVCODEC_63",
        }
        missing_rpu = sorted(required_rpu - required)
        if missing_rpu:
            raise SystemExit(f"{abi}: staged Dolby Vision bridge lacks expected RPU imports: {missing_rpu}")

        print(f"{abi}: official ISO bridge isolated; Dolby Vision FFmpeg linkage verified")


if __name__ == "__main__":
    main()
