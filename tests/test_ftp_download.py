"""Regression tests for recursive camera downloads and local file handling."""

import ftplib
import ssl
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from cognex_discovery.discovery import CognexDevice
from cognex_discovery.ftp_download import download_camera, safe_folder_name


class FakeFTP:
    def __init__(self, *, legacy=False, fail=False, cancel=None, unsafe=False):
        self.current = "/"
        self.legacy = legacy
        self.fail = fail
        self.cancel = cancel
        self.unsafe = unsafe
        self.login_args = None
        self.commands = []
        self.closed = False
        self.tree = {
            "/": {"job.bin": b"\x00\xff\r\n", "images": None},
            "/images": {"sample.bmp": b"image"},
        }

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def connect(self, host, port):
        self.connection = (host, port)

    def auth(self):
        self.commands.append("AUTH TLS")

    def login(self, user, password):
        self.commands.append("LOGIN")
        self.login_args = (user, password)

    def prot_p(self):
        self.commands.append("PROT P")

    def pwd(self):
        return self.current

    def cwd(self, path):
        target = path if path.startswith("/") else self.current.rstrip("/") + "/" + path
        if target not in self.tree:
            raise ftplib.error_perm("550 Not a directory")
        self.current = target

    def mlsd(self):
        if self.legacy:
            raise ftplib.error_perm("502 Not implemented")
        if self.unsafe:
            return [("../escape", {"type": "file"})]
        return [(".", {"type": "cdir"}), ("..", {"type": "pdir"})] + [
            (name, {"type": "dir" if data is None else "file"})
            for name, data in self.tree[self.current].items()
        ]

    def nlst(self):
        return [
            self.current.rstrip("/") + "/" + name for name in self.tree[self.current]
        ]

    def retrbinary(self, command, callback, blocksize):
        data = self.tree[self.current][command.removeprefix("RETR ")]
        if self.cancel:
            self.cancel.set()
        callback(data)
        if self.fail:
            raise OSError("Disconnected")


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name)
        self.device = CognexDevice(
            device_name="Line 1 Camera", ip_address="192.168.1.10"
        )

    def download(self, ftp, cancel=None, **options):
        factory = "FTP_TLS" if options.get("use_tls", True) else "FTP"
        with patch(f"cognex_discovery.ftp_download.ftplib.{factory}", return_value=ftp):
            return download_camera(
                self.device,
                self.destination,
                cancel or threading.Event(),
                lambda text: None,
                **options,
            )

    def test_binary_root_and_subfolders_with_both_listing_protocols(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                ftp = FakeFTP(legacy=legacy)
                result = self.download(ftp)
                self.assertEqual(result.error, "")
                self.assertEqual(result.files, 2)
                self.assertEqual(result.bytes_copied, 9)
                self.assertEqual(
                    (result.folder / "job.bin").read_bytes(), b"\x00\xff\r\n"
                )
                self.assertEqual(
                    (result.folder / "images/sample.bmp").read_bytes(), b"image"
                )
                self.assertEqual(ftp.login_args, ("admin", ""))
                self.assertEqual(ftp.connection, ("192.168.1.10", 21))
                self.assertTrue(ftp.closed)

    def test_failures_identify_connection_login_and_listing_stages(self):
        for method, error, expected in [
            (
                "connect",
                ConnectionRefusedError("refused"),
                "connecting to 192.168.1.10:21",
            ),
            ("login", ftplib.error_perm("530 Login incorrect"), "logging in"),
            ("mlsd", TimeoutError("timed out"), "listing / (passive FTP)"),
        ]:
            with self.subTest(method=method):
                ftp = FakeFTP()
                setattr(ftp, method, Mock(side_effect=error))
                result = self.download(ftp)
                self.assertIn(expected, result.error)
                self.assertIn(str(error), result.error)

    def test_tls_is_enabled_before_login_and_data_is_protected(self):
        ftp = FakeFTP()
        result = self.download(ftp)
        self.assertEqual(result.error, "")
        self.assertEqual(ftp.commands, ["AUTH TLS", "LOGIN", "PROT P"])

    def test_plain_ftp_is_only_used_when_selected(self):
        ftp = FakeFTP()
        result = self.download(ftp, use_tls=False)
        self.assertEqual(result.error, "")
        self.assertEqual(ftp.commands, ["LOGIN"])

    def test_tls_certificate_policy_and_explicit_self_signed_option(self):
        for verify in (True, False):
            with self.subTest(verify=verify):
                with patch(
                    "cognex_discovery.ftp_download.ftplib.FTP_TLS",
                    return_value=FakeFTP(),
                ) as factory:
                    download_camera(
                        self.device,
                        self.destination,
                        threading.Event(),
                        lambda text: None,
                        verify_certificate=verify,
                    )
                context = factory.call_args.kwargs["context"]
                self.assertEqual(context.check_hostname, verify)
                self.assertEqual(
                    context.verify_mode, ssl.CERT_REQUIRED if verify else ssl.CERT_NONE
                )

    def test_failed_tls_never_sends_login_or_falls_back_to_plain_ftp(self):
        ftp = FakeFTP()
        ftp.auth = Mock(
            side_effect=ssl.SSLCertVerificationError("self-signed certificate")
        )
        with patch("cognex_discovery.ftp_download.ftplib.FTP") as plain:
            result = self.download(ftp)
        self.assertIn("negotiating TLS", result.error)
        self.assertIn("certificate", result.error)
        self.assertIsNone(ftp.login_args)
        plain.assert_not_called()

    def test_existing_camera_folder_is_preserved(self):
        previous = self.destination / self.device.device_name
        previous.mkdir()
        (previous / "job.bin").write_bytes(b"old")
        result = self.download(FakeFTP())
        self.assertEqual(result.folder.parent, previous)
        self.assertRegex(result.folder.name, r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")
        self.assertEqual((previous / "job.bin").read_bytes(), b"old")

    def test_local_timestamp_and_same_second_backups_preserve_both(self):
        with patch("cognex_discovery.ftp_download.datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 15, 14, 30, 5)
            first = self.download(FakeFTP())
            second = self.download(FakeFTP())
        self.assertEqual(first.folder.parent.name, self.device.device_name)
        self.assertEqual(first.folder.name, "2026-10-15_14-30-05")
        self.assertEqual(second.folder.parent, first.folder.parent)
        self.assertEqual(second.folder.name, "2026-10-15_14-30-05 (2)")
        self.assertEqual((first.folder / "job.bin").read_bytes(), b"\x00\xff\r\n")
        self.assertEqual((second.folder / "job.bin").read_bytes(), b"\x00\xff\r\n")

    def test_camera_folder_that_is_a_file_is_not_overwritten(self):
        camera = self.destination / self.device.device_name
        camera.write_bytes(b"keep")
        result = self.download(FakeFTP())
        self.assertTrue(result.error)
        self.assertEqual(camera.read_bytes(), b"keep")

    def test_failed_file_is_not_left_as_a_completed_or_partial_download(self):
        result = self.download(FakeFTP(fail=True))
        self.assertIn("Disconnected", result.error)
        self.assertEqual(list(result.folder.iterdir()), [])

    def test_cancel_during_transfer_removes_partial_file_and_closes_connection(self):
        cancel = threading.Event()
        ftp = FakeFTP(cancel=cancel)
        result = self.download(ftp, cancel)
        self.assertTrue(result.cancelled)
        self.assertEqual(list(result.folder.iterdir()), [])
        self.assertTrue(ftp.closed)

    def test_unsafe_remote_path_cannot_escape_camera_folder(self):
        result = self.download(FakeFTP(unsafe=True))
        self.assertIn("Unsupported remote file name", result.error)
        self.assertFalse((self.destination / "escape").exists())

    def test_invalid_and_reserved_device_names_are_sanitized(self):
        for value, expected in [
            ("Line:1/Camera", "Line_1_Camera"),
            ("CON", "_CON"),
            ("..", "Camera"),
            ("Camera. ", "Camera"),
        ]:
            self.assertEqual(safe_folder_name(value), expected)

    def test_cancel_before_connecting_creates_nothing(self):
        cancel = threading.Event()
        cancel.set()
        ftp = FakeFTP()
        result = self.download(ftp, cancel)
        self.assertTrue(result.cancelled)
        self.assertIsNone(ftp.login_args)
        self.assertEqual(list(self.destination.iterdir()), [])
