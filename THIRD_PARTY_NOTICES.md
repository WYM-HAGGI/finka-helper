# Third-party components

This archive distributes the helper's source and configuration. Python runtimes and packages are obtained from their upstream releases or PyPI; the documented portable ipatool build is obtained from this project's Release. The helper is not affiliated with Apple or the Finka app developer.

| Component | Version | Source / license |
|---|---|---|
| ipatool portable build | 2.7.0-finka-portable.1 | Based on [majd/ipatool 2.7.0](https://github.com/majd/ipatool/releases/tag/v2.7.0), [MIT](https://github.com/majd/ipatool/blob/v2.7.0/LICENSE); portability build script in tools/build_portable_ipatool.py |
| CPython portable builds | 3.13.17, build 20261021 | [Astral python-build-standalone](https://github.com/astral-sh/python-build-standalone/releases/tag/20261021); build tooling [MPL-2.0](https://github.com/astral-sh/python-build-standalone/blob/main/LICENSE); Python and bundled libraries retain their own licenses |
| pymobiledevice3 | 11.43.1 | [doronz88/pymobiledevice3](https://github.com/doronz88/pymobiledevice3), [GPL-3.0](https://github.com/doronz88/pymobiledevice3/blob/master/LICENSE) |
| pywinpty (Windows) | 3.1.0 | [andfoy/pywinpty](https://github.com/andfoy/pywinpty), [MIT](https://github.com/andfoy/pywinpty/blob/main/LICENSE.txt) |
| Other Python dependencies | See requirements-usb.txt | [PyPI](https://pypi.org/); package metadata and installed .dist-info license files |
| Apple Devices | Installed by the user when needed | [Official Microsoft Store listing](https://apps.microsoft.com/detail/9NP83LWLPZ9K); Apple terms apply |

Pinned upstream runtime and this project's portable-tool release URLs, sizes and SHA-256 values are recorded in `manifest.json`. Python dependencies are installed into the bundle's .finka-work environment. Only the USB modules used for pairing and installation are prepared. Upstream license files included with downloaded runtimes and installed packages remain in place.

The portable ipatool build is modified by Finka Helper contributors. It requires an explicit absolute private state directory and uses only the original encrypted file backend. The build copies the unmodified file-backend/core files from byteness/keyring 1.9.0 (MIT), excluding unused OS and 1Password backends, and is built without CGO. No Apple authentication, entitlement or download algorithm is changed. Source input SHA-256 is pinned in the build script; dependency checksums remain under Go module verification. Each portable archive includes upstream LICENSE, KEYRING_LICENSE and a modification notice.

The USB worker saves newly created local pairing copies inside .finka-work/pairing and uses the SDK base persistence method instead of the subclass that also writes a shared usbmux copy. Existing system records may be read but are not deleted. Apple Devices and device trust are outside workspace deletion.

The Finka application itself is downloaded from Apple for the authenticated user's account, not bundled or licensed by this helper.

Reference: [Apple verification codes](https://support.apple.com/102606), [Windows on ARM emulation](https://learn.microsoft.com/en-us/windows/arm/apps-on-arm-x86-emulation).
