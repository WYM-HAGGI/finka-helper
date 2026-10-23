# Third-party components

This archive distributes the helper's source and configuration. External executables and Python packages are obtained from their upstream release sites or PyPI at runtime, without modification. The helper is not affiliated with Apple or the Finka app developer.

| Component | Version | Source / license |
|---|---|---|
| ipatool | 2.7.0 | [majd/ipatool](https://github.com/majd/ipatool/releases/tag/v2.7.0), [MIT](https://github.com/majd/ipatool/blob/v2.7.0/LICENSE) |
| CPython portable builds | 3.13.17, build 20261021 | [Astral python-build-standalone](https://github.com/astral-sh/python-build-standalone/releases/tag/20261021); build tooling [MPL-2.0](https://github.com/astral-sh/python-build-standalone/blob/main/LICENSE); Python and bundled libraries retain their own licenses |
| pymobiledevice3 | 11.43.1 | [doronz88/pymobiledevice3](https://github.com/doronz88/pymobiledevice3), [GPL-3.0](https://github.com/doronz88/pymobiledevice3/blob/master/LICENSE) |
| pywinpty (Windows) | 3.1.0 | [andfoy/pywinpty](https://github.com/andfoy/pywinpty), [MIT](https://github.com/andfoy/pywinpty/blob/main/LICENSE.txt) |
| Other Python dependencies | See requirements-usb.txt | [PyPI](https://pypi.org/); package metadata and installed .dist-info license files |
| Apple Devices | Installed by the user when needed | [Official Microsoft Store listing](https://apps.microsoft.com/detail/9NP83LWLPZ9K); Apple terms apply |

Pinned official executable URLs, release sizes and SHA-256 values are recorded in `manifest.json`. Python dependencies are installed into this helper's isolated environment. Only the USB modules used for pairing and installation are prepared; firmware, backups and developer debugging modules are outside this helper's scope. Upstream license files included with downloaded runtimes and installed packages remain in place.

The Finka application itself is downloaded from Apple for the authenticated user's account, not bundled or licensed by this helper.

Reference: [Apple verification codes](https://support.apple.com/102606), [Windows on ARM emulation](https://learn.microsoft.com/en-us/windows/arm/apps-on-arm-x86-emulation).
