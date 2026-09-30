"""Stage verified official 5.6.7 native payloads for compatibility testing.

The preferred input is the previously verified Actions inspection artifact.
The source-built libmedia3ass.so is intentionally kept instead of replacing it
with the official binary because its Java/JNI interface is not source-compatible.
"""

import argparse
import hashlib
import json
import struct
import zipfile
from pathlib import Path

APK_SHA256 = {
    "arm64-v8a": "5428c11d5aa09813dfa2fde8a32be2beeeddfc45d093b89e46ed53cd696a3b3c",
    "armeabi-v7a": "492bc0b1d4607a41d3c3f56d37bc590be0ee6b8b481bebe52f6dd2b0819d2385",
}

NATIVE_FILES = (
    "libisoJNI.so",
    "libffmpegDoviJNI.so",
    "libcmg_decrypt.so",
    "libmedia3effect.so",
)

BDJ_FILES = (
    "libbluray-j2se-1.4.1.jar",
    "libbluray-awt-j2se-1.4.1.jar",
)

EXPECTED_ELF = {
    "arm64-v8a": (2, 183),
    "armeabi-v7a": (1, 40),
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
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


def copy_record(data: bytes, target: Path) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {
        "path": target.as_posix(),
        "bytes": len(data),
        "sha256": sha256_bytes(data),
    }


def extract_from_apk(apk: Path, abi: str, app_root: Path, bdj_reference: dict[str, bytes]) -> list[dict]:
    expected_hash = APK_SHA256[abi]
    actual_hash = sha256_file(apk)
    if actual_hash != expected_hash:
        raise ValueError(
            f"Official {abi} APK SHA256 mismatch: expected {expected_hash}, got {actual_hash}"
        )

    records: list[dict] = []
    with zipfile.ZipFile(apk) as archive:
        for name in NATIVE_FILES:
            data = archive.read(f"lib/{abi}/{name}")
            verify_elf(data, abi, name)
            records.append(copy_record(
                data, app_root / "src" / "main" / "jniLibs" / abi / name
            ))
        for name in BDJ_FILES:
            data = archive.read(f"assets/bdj/{name}")
            previous = bdj_reference.get(name)
            if previous is not None and previous != data:
                raise ValueError(f"Official APKs disagree about BD-J asset {name}")
            bdj_reference[name] = data
    return records


def load_inspection_records(root: Path) -> tuple[dict, dict[str, dict]]:
    report_path = root / "inspection.json"
    if not report_path.is_file():
        matches = list(root.rglob("inspection.json"))
        if len(matches) != 1:
            raise ValueError(f"Expected one inspection.json under {root}, found {len(matches)}")
        report_path = matches[0]
        root = report_path.parent

    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("version") != "5.6.6":
        raise ValueError(f"Unexpected inspection version: {report.get('version')}")

    indexed: dict[str, dict] = {}
    for abi_data in report.get("official_files", {}).values():
        for item in abi_data.get("files", []):
            indexed[item["path"]] = item
    return report, indexed


def verified_artifact_bytes(root: Path, relative: str, indexed: dict[str, dict]) -> bytes:
    path = root / relative
    if not path.is_file():
        raise ValueError(f"Missing inspection payload: {relative}")
    data = path.read_bytes()
    record = indexed.get(relative)
    if record is None:
        raise ValueError(f"inspection.json has no record for {relative}")
    if len(data) != int(record["bytes"]) or sha256_bytes(data) != record["sha256"]:
        raise ValueError(f"Inspection payload hash/size mismatch: {relative}")
    return data


def stage_from_inspection(root: Path, app_root: Path) -> dict[str, list[dict]]:
    report_path = root / "inspection.json"
    if not report_path.is_file():
        matches = list(root.rglob("inspection.json"))
        if len(matches) != 1:
            raise ValueError(f"Expected one inspection.json under {root}, found {len(matches)}")
        root = matches[0].parent

    _, indexed = load_inspection_records(root)
    records: dict[str, list[dict]] = {}

    for abi in EXPECTED_ELF:
        records[abi] = []
        for name in NATIVE_FILES:
            relative = f"jniLibs/{abi}/{name}"
            data = verified_artifact_bytes(root, relative, indexed)
            verify_elf(data, abi, name)
            records[abi].append(copy_record(
                data, app_root / "src" / "main" / "jniLibs" / abi / name
            ))

    records["bdj"] = []
    for name in BDJ_FILES:
        relative = f"assets/bdj/{name}"
        data = verified_artifact_bytes(root, relative, indexed)
        records["bdj"].append(copy_record(
            data, app_root / "src" / "main" / "assets" / "bdj" / name
        ))
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm64", type=Path)
    parser.add_argument("--armeabi", type=Path)
    parser.add_argument("--inspection-root", type=Path)
    parser.add_argument("--app-root", type=Path, default=Path("app"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    app_root = args.app_root.resolve()

    if args.inspection_root:
        if args.arm64 or args.armeabi:
            parser.error("--inspection-root cannot be combined with APK inputs")
        records = stage_from_inspection(args.inspection_root.resolve(), app_root)
        source = "verified-actions-inspection-artifact"
    else:
        if not args.arm64 or not args.armeabi:
            parser.error("provide --inspection-root or both --arm64 and --armeabi")
        bdj_payloads: dict[str, bytes] = {}
        records = {}
        records["arm64-v8a"] = extract_from_apk(args.arm64, "arm64-v8a", app_root, bdj_payloads)
        records["armeabi-v7a"] = extract_from_apk(args.armeabi, "armeabi-v7a", app_root, bdj_payloads)
        records["bdj"] = []
        for name, data in bdj_payloads.items():
            records["bdj"].append(copy_record(
                data, app_root / "src" / "main" / "assets" / "bdj" / name
            ))
        source = "official-5.6.7-apks"

    total_bytes = sum(item["bytes"] for group in records.values() for item in group)
    out = {
        "official_version": "5.6.7" if source == "official-5.6.7-apks" else "5.6.6",
        "source": source,
        "staged_native_files": list(NATIVE_FILES),
        "staged_bdj_files": list(BDJ_FILES),
        "kept_source_built_libmedia3ass": True,
        "total_staged_bytes": total_bytes,
        "records": records,
        "warning": (
            "Payload presence is a packaging milestone only. Missing Java/Media3 call chains "
            "still need to be restored and tested on ARM hardware."
        ),
    }

    report_path = args.report or (app_root.parent / "official-native-stage.json")
    report_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Staged verified 5.6.7 payloads: {total_bytes} bytes")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
