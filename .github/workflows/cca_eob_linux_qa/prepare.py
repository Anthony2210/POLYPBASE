"""Authenticate non-QA bytes, then export only TARGET objects and exact QA inputs."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

TARGET = "5461e7a5c07cf8893c2db78953316df78c9243a8"
TARGET_ARCHIVE_SHA256 = "bc6cc0bad2e7b63c70bf448d23c393701a270d81d98674a28fc21e6beb37fffc"
REFERENCE = "13a83602ab67f3ac9092ce7cd7207e0dbe5efe50"
ROOT = Path(__file__).resolve().parents[3]
QA = ".github/workflows/cca_eob_linux_qa/"
ALLOW = frozenset({
    ".github/workflows/cca-eob-linux-qa.yml",
    *(QA + name for name in ("Dockerfile", "run_tests.py", "prove_installation.py",
                            "prepare.py", "run_container.py", "test_qa.py", "README.md")),
})


def git(*args, data=None):
    return subprocess.run(["git", "--no-pager", *args], cwd=ROOT, input=data,
                          capture_output=True, check=True, timeout=120).stdout


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify():
    changed = set(filter(None, git("diff", "--name-only", "-z", TARGET, "HEAD").decode().split("\0")))
    changed.update(filter(None, git("diff", "--name-only", "-z", "HEAD").decode().split("\0")))
    changed.update(filter(None, git("ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")))
    require(changed <= ALLOW, "Unauthorized delta: " + repr(sorted(changed - ALLOW)))
    expected = {}
    for record in git("ls-tree", "-rz", "--full-tree", TARGET).split(b"\0"):
        if not record:
            continue
        header, raw_name = record.split(b"\t", 1)
        mode, kind, oid = header.decode().split()
        name = raw_name.decode()
        require(kind == "blob" and mode in ("100644", "100755"), "Unsupported tracked entry: " + name)
        if name in ALLOW:
            continue
        path = ROOT / name
        require(path.is_file() and not path.is_symlink(), "Missing/nonregular source: " + name)
        content = path.read_bytes()
        require(hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest() == oid,
                "Byte-for-byte TARGET mismatch (including line endings): " + name)
        expected[name] = hashlib.sha256(content).hexdigest()
    require(all((ROOT / name).is_file() and not (ROOT / name).is_symlink() for name in ALLOW),
            "QA allowlist incomplete or contains aliases")
    return {"target": TARGET, "qa_head": git("rev-parse", "HEAD").decode().strip(),
            "reference_infrastructure": REFERENCE, "exact_qa_allowlist": sorted(ALLOW),
            "non_qa_sha256": expected, "status": "passed"}


def target_archive():
    # git archive can apply eol=crlf even on Linux. Read raw blobs instead, never
    # silently convert application/dependency bytes through checkout attributes.
    records = []
    for record in git('ls-tree', '-rz', '--full-tree', TARGET).split(b'\0'):
        if record:
            header, name = record.split(b'\t', 1)
            mode, kind, oid = header.decode().split()
            require(kind == 'blob' and mode in ('100644', '100755'), 'Unsupported archive entry')
            records.append((name.decode(), mode, oid))
    blobs = io.BytesIO(git('cat-file', '--batch', data=(
        '\n'.join(oid for _, _, oid in records) + '\n').encode()))
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        for name, mode, oid in records:
            actual, kind, size = blobs.readline().decode().strip().split()
            require(actual == oid and kind == 'blob', 'Git blob batch identity mismatch')
            content = blobs.read(int(size))
            require(blobs.read(1) == b'\n' and len(content) == int(size), 'Truncated Git blob')
            require(hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest() == oid,
                    'Exported TARGET blob hash mismatch: ' + name)
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o755 if mode == '100755' else 0o644
            member.uid = member.gid = member.mtime = 0
            archive.addfile(member, io.BytesIO(content))
    require(not blobs.read(), 'Unexpected Git batch data')
    data = output.getvalue()
    require(hashlib.sha256(data).hexdigest() == TARGET_ARCHIVE_SHA256,
            'Canonical TARGET archive identity mismatch')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    evidence = verify()
    if args.output:
        destination = args.output.resolve()
        require(not destination.exists() and not destination.is_relative_to(ROOT),
                "Build context must be new and outside the checkout")
        destination.mkdir(parents=True)
        archive = target_archive()
        (destination / "target.tar").write_bytes(archive)
        # Only the exact target commit, its trees and blobs; no history or local files.
        records = git("ls-tree", "-rtz", TARGET).split(b"\0")
        objects = {TARGET, git("rev-parse", TARGET + "^{tree}").decode().strip()}
        objects.update(record.split(b"\t", 1)[0].decode().split()[2] for record in records if record)
        pack = git("pack-objects", "--stdout", data=("\n".join(sorted(objects)) + "\n").encode())
        (destination / "target.pack").write_bytes(pack)
        evidence.update(target_archive_sha256=hashlib.sha256(archive).hexdigest(),
                        target_pack_sha256=hashlib.sha256(pack).hexdigest())
        evidence["qa_sha256"] = {}
        for name in sorted(ALLOW):
            content = (ROOT / name).read_bytes()
            evidence["qa_sha256"][name] = hashlib.sha256(content).hexdigest()
            if name.startswith(QA):
                shutil.copyfile(ROOT / name, destination / Path(name).name)
        (destination / "source-integrity.json").write_text(
            json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "target": TARGET,
                      "verified_non_qa_files": len(evidence["non_qa_sha256"]),
                      "context": str(args.output) if args.output else None}, sort_keys=True))


if __name__ == "__main__":
    main()
