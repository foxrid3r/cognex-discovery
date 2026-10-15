"""Read-only recursive FTP downloads, independent of the desktop interface."""

import ftplib
import os
import re
import ssl
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv4Address
from pathlib import Path, PurePosixPath

from .discovery import CognexDevice


class DownloadCancelled(Exception):
    """The user requested cancellation."""


def safe_folder_name(name: str) -> str:
    """Make a device name usable as a Windows folder name."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(". ")
    if not name or name in {".", ".."}:
        name = "Camera"
    if name.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        name = "_" + name
    return name


def create_camera_folder(destination: Path, name: str) -> Path:
    """Reserve a local-time backup folder under the reusable camera folder."""
    base = safe_folder_name(name)[:100].rstrip(". ")
    camera = destination / base
    # Reuse camera folders, but never follow a link outside the destination.
    if camera.is_symlink() or camera.resolve().parent != destination.resolve():
        raise ValueError(f"Camera folder must be inside the destination: {camera}")
    camera.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    for number in range(1, 100000):
        candidate = camera / (timestamp if number == 1 else f"{timestamp} ({number})")
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            continue
    raise OSError(f"Could not create a unique backup folder for {base}")


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise DownloadCancelled()


def _local_child(parent: Path, name: str) -> Path:
    # Do not silently rename remote files: that could merge distinct paths.
    if name in {"", ".", ".."} or safe_folder_name(name) != name:
        raise ValueError(f"Unsupported remote file name: {name!r}")
    child = parent / name
    if child.exists() or child.is_symlink():
        raise ValueError(f"Duplicate remote path: {name!r}")
    return child


def _entries(ftp: ftplib.FTP) -> list[tuple[str, str]]:
    try:
        entries = list(ftp.mlsd())
    except ftplib.error_perm as exc:
        if not str(exc).startswith(("500", "501", "502", "504")):
            raise
        # Older camera servers may only implement NLST and CWD.
        current = ftp.pwd()
        result = []
        for path in ftp.nlst():
            name = PurePosixPath(path).name
            if name in {".", "..", ""}:
                continue
            # Some servers return absolute names; accept only direct children.
            if "/" in path and path not in {
                "./" + name,
                str(PurePosixPath(current) / name),
            }:
                raise ValueError(f"Unexpected directory entry: {path!r}")
            try:
                ftp.cwd(name)
            except ftplib.error_perm as exc:
                if not str(exc).startswith("550"):
                    raise
                result.append((name, "file"))
            else:
                ftp.cwd(current)
                result.append((name, "dir"))
        return result
    result = []
    for name, facts in entries:
        kind = facts.get("type", "").lower()
        if kind in {"cdir", "pdir"}:
            continue
        if kind not in {"file", "dir"}:
            raise ValueError(f"Unsupported FTP entry type for {name!r}: {kind}")
        result.append((name, kind))
    return result


@dataclass
class DownloadResult:
    device: CognexDevice
    folder: Path | None = None
    files: int = 0
    bytes_copied: int = 0
    error: str = ""
    cancelled: bool = False


def download_camera(
    device: CognexDevice,
    destination: Path,
    cancel: threading.Event,
    progress: Callable[[str], None],
    *,
    username: str = "admin",
    password: str = "",
    source_ip: str = "",
    timeout: float = 10.0,
    use_tls: bool = True,
    verify_certificate: bool = False,
) -> DownloadResult:
    """Copy the FTP root into a timestamped camera backup using binary transfers.

    Completed files remain after failure/cancellation; incomplete files are removed.
    Connections and reads time out so cancellation also works on unresponsive cameras.
    """
    result = DownloadResult(device)
    visited = set()
    label = device.device_name or device.ip_address
    stage = "validating settings"
    try:
        _check_cancel(cancel)
        host = str(IPv4Address(device.ip_address))
        destination = Path(destination).resolve(strict=True)
        options = dict(
            timeout=timeout, source_address=(source_ip, 0) if source_ip else None
        )
        if use_tls:
            context = ssl.create_default_context()
            if not verify_certificate:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            client = ftplib.FTP_TLS(context=context, **options)
        else:
            client = ftplib.FTP(**options)
        with client as ftp:
            stage = f"connecting to {host}:21"
            interface = source_ip or "OS default"
            progress(f"{label}: {stage} via {interface}...")
            ftp.connect(host, 21)
            progress(f"{label}: connected; logging in as {username}...")
            stage = f"logging in to {host}:21 as {username}"
            if use_tls:
                stage = f"negotiating TLS with {host}:21"
                ftp.auth()
                stage = f"logging in to {host}:21 as {username} over TLS"
            ftp.login(username, password)
            if use_tls:
                stage = "enabling encrypted data transfers (PROT P)"
                ftp.prot_p()
            progress(f"{label}: login successful; opening FTP root...")
            stage = "opening FTP root (CWD /)"
            ftp.cwd("/")
            stage = "creating local backup folder"
            result.folder = create_camera_folder(
                destination, device.device_name or device.ip_address
            )
            progress(f"{label}: saving to {result.folder}")

            def copy_directory(local: Path, depth: int = 0) -> None:
                _check_cancel(cancel)
                nonlocal stage
                stage = "reading current FTP directory (PWD)"
                remote = ftp.pwd()
                if remote in visited or depth > 64:
                    raise ValueError(f"Directory loop or excessive depth at {remote}")
                visited.add(remote)
                stage = f"listing {remote} (passive FTP)"
                progress(f"{label}: {stage}...")
                for name, kind in _entries(ftp):
                    _check_cancel(cancel)
                    stage = f"preparing {PurePosixPath(remote) / name}"
                    target = _local_child(local, name)
                    if kind == "dir":
                        target.mkdir()
                        stage = f"opening directory {PurePosixPath(remote) / name}"
                        ftp.cwd(name)
                        copy_directory(target, depth + 1)
                        stage = f"returning to directory {remote}"
                        ftp.cwd(remote)
                    else:
                        stage = f"copying {PurePosixPath(remote) / name}"
                        progress(f"{label}: {stage}")
                        temporary = None
                        try:
                            with tempfile.NamedTemporaryFile(
                                dir=local, prefix=".download-", delete=False
                            ) as output:
                                temporary = Path(output.name)

                                def write(block: bytes) -> None:
                                    _check_cancel(cancel)
                                    output.write(block)
                                    result.bytes_copied += len(block)

                                ftp.retrbinary(f"RETR {name}", write, blocksize=65536)
                            _check_cancel(cancel)
                            # The destination is a new private backup tree.
                            os.replace(temporary, target)
                            result.files += 1
                        finally:
                            if temporary is not None:
                                temporary.unlink(missing_ok=True)

            copy_directory(result.folder)
            stage = "closing FTP connection"
    except DownloadCancelled:
        result.cancelled = True
    except Exception as exc:
        if cancel.is_set():
            result.cancelled = True
        else:
            result.error = f"{stage}: {type(exc).__name__}: {exc}"
            if isinstance(exc, ssl.SSLCertVerificationError):
                result.error += (
                    ". The server certificate could not be verified. For a known camera "
                    "with a self-signed certificate, you can explicitly disable certificate "
                    "verification in Download settings."
                )
    return result
