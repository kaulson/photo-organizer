"""Drive UUID detection utilities."""

import subprocess
from pathlib import Path


class DriveUUIDError(Exception):
    """Raised when drive UUID cannot be determined."""


def get_drive_uuid(mount_point: str | Path) -> str:
    """Get the UUID of the drive containing the given path."""
    mount_point = str(Path(mount_point).resolve())
    device = _get_device_for_mount(mount_point)
    uuid = _get_uuid_for_device(device)
    return uuid


def _get_device_for_mount(path: str) -> str:
    result = subprocess.run(
        ["findmnt", "-n", "-o", "SOURCE", "-T", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise DriveUUIDError(f"Could not find mount point for path: {path}")

    device = result.stdout.strip()
    if not device:
        raise DriveUUIDError(f"No device found for path: {path}")

    return device


def _get_uuid_for_device(device: str) -> str:
    result = subprocess.run(
        ["lsblk", "-n", "-o", "UUID", device],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise DriveUUIDError(f"Could not get UUID for device: {device}")

    uuid = result.stdout.strip()
    if not uuid:
        raise DriveUUIDError(
            f"No UUID found for device: {device}. "
            "This may be a network share or virtual filesystem."
        )

    return uuid
