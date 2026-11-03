"""Run the prepared image offline; preserve streamed evidence without host mounts."""

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import uuid

TARGET = "cb07a2cea43e4af0bc9a8a61ed33abaa23eef1cc"
MARKER = b"CCA_EOB_QA_EVIDENCE_V1:"


def docker(*args, check=True, timeout=120):
    return subprocess.run(["docker", *args], capture_output=True, check=check, timeout=timeout)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def unpack_evidence(stdout, output):
    """Read only regular data files from the QA stdout protocol, never extract links."""
    frames = [line[len(MARKER):] for line in stdout.splitlines() if line.startswith(MARKER)]
    require(len(frames) == 1, "Require exactly one complete evidence frame before container exit")
    packed = base64.b64decode(frames[0], validate=True)
    require(len(packed) < 32 * 1024 * 1024, "Oversize evidence archive")
    seen, total = set(), 0
    with tarfile.open(fileobj=io.BytesIO(packed), mode="r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            total += member.size
            require(member.isfile() and not path.is_absolute() and ".." not in path.parts
                                and "\\" not in member.name and ":" not in member.name
                    and member.name not in seen and str(path) == member.name
                    and 0 <= member.size <= 16 * 1024 * 1024 and total <= 64 * 1024 * 1024,
                    "Unsafe/duplicate/oversize evidence member")
            require((len(path.parts) == 1 and path.name in
                     {"core-tests.json", "source-integrity.json", "install-receipt.json"})
                    or (len(path.parts) == 2 and path.parts[0] == "installation"),
                    "Unexpected evidence destination")
            seen.add(member.name)
            destination = output / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(archive.extractfile(member).read())
    return seen


def validate_success(output):
    core = json.loads((output / "core-tests.json").read_bytes())
    require(core["status"] == "passed" and core["expected_counts"] == [59, 6, 14]
            and core["zero_skips_required"] is True, "Mandatory test summary missing/failed")
    installation = output / "installation"
    summary = json.loads((installation / "summary.json").read_bytes())
    require(summary["status"] == "passed" and summary["target"] == TARGET
                and summary["qa_only"] is True
            and summary["production"] is False and summary["active_manifest_installed"] is False
            and summary.get("resource_closure") == "passed"
                        and summary.get("startup_delta") == "passed"
                        and summary.get("tracked_runtime_sources") == 249
                        and summary.get("manifest_files") == summary.get("expected_manifest_files") == 955
            and summary.get("changed_resources") == ["backend/locale/fr/LC_MESSAGES/django.mo"],
            "Complete installation proof missing/failed")
    for name in ("trace-v2.json", "inventory.json", "report.txt", "qa-only-manifest.json",
                 "offline-audit.json", "independent-tracked-sources.json",
                 "uv-locked-environment-check.exit.json", "installed-lock-membership.json",
                 "resource-closure.json", "startup-delta.json"):
        require(name in summary["artifact_sha256"], "Required installation evidence absent: " + name)
    for name, expected in summary["artifact_sha256"].items():
        require(PurePosixPath(name).name == name
                and hashlib.sha256((installation / name).read_bytes()).hexdigest() == expected,
                "Exported artifact integrity failure: " + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    require(not args.output.exists(), "Output directory must be new")
    args.output.mkdir(parents=True)
    name = "cca-eob-qa-" + uuid.uuid4().hex
    created = False
    try:
        image = json.loads(docker("image", "inspect", args.image).stdout)[0]
        require(image["Os"] == "linux" and image["Architecture"] == "amd64", "Require Linux amd64 image")
        (args.output / "image-inspect.json").write_text(json.dumps(image, indent=2) + "\n")
        docker("create", "--name", name, "--pull=never", "--platform=linux/amd64",
               "--network=none", "--read-only", "--user=10001:10001", "--cap-drop=ALL",
               "--security-opt=no-new-privileges:true", "--pids-limit=256", "--memory=2g", "--cpus=2",
               "--tmpfs=/tmp:rw,exec,nosuid,nodev,size=512m,mode=1777", image["Id"])
        created = True
        inspect = json.loads(docker("inspect", name).stdout)[0]
        (args.output / "container-inspect.json").write_text(json.dumps(inspect, indent=2) + "\n")
        host = inspect["HostConfig"]
        require(host["NetworkMode"] == "none" and host["ReadonlyRootfs"] is True
                and not host["Privileged"] and host["CapDrop"] == ["ALL"]
                and "no-new-privileges:true" in host["SecurityOpt"]
                and not host["Binds"] and not host["Devices"] and not host["CapAdd"]
                and all(mount["Type"] == "tmpfs" and mount["Destination"] == "/tmp"
                        for mount in inspect["Mounts"])
                and set(host["Tmpfs"]) == {"/tmp"}
                and inspect["Config"]["User"] == "10001:10001"
                and inspect["Image"] == image["Id"], "Container isolation inspection refused")
        # No -e, --env-file, bind mounts, socket, credentials, or production inputs.
        try:
            run = docker("start", "--attach", name, check=False, timeout=900)
        except subprocess.TimeoutExpired as error:
            (args.output / "execution.stdout").write_bytes(error.stdout or b"")
            (args.output / "execution.stderr").write_bytes(error.stderr or b"")
            raise
        # Keep the frame out of ordinary logs; unpack only the regular evidence files.
        log = b"\n".join(line for line in run.stdout.splitlines() if not line.startswith(MARKER)) + b"\n"
        (args.output / "execution.stdout").write_bytes(log)
        (args.output / "execution.stderr").write_bytes(run.stderr)
        print(log.decode(errors="replace"), end="")
        print(run.stderr.decode(errors="replace"), end="", file=sys.stderr)
        unpack_evidence(run.stdout, args.output)
        state = json.loads(docker("inspect", name).stdout)[0]["State"]
        (args.output / "container-state.json").write_text(json.dumps(state, indent=2) + "\n")
        require(not state["Running"] and not state["OOMKilled"] and not state["Error"],
                "Container did not finish cleanly")
        require(run.returncode == 0 and state["ExitCode"] == 0, "Isolated QA failed")
        validate_success(args.output)
        return 0
    finally:
        if created:
            docker("rm", "--force", name, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
