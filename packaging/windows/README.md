# Nectivon 0.8.5 Windows x64 packaging

> **UNSIGNED ENGINEERING BUILD — not a public release and not SmartScreen-trusted.**

This directory builds a per-user x64 MSI with WiX Toolset. The installed application is
offline-capable and does not require system Python, Git, Node, an IDE, Codex, or WorkBuddy.

## Fixed layout and lifecycle

- Program files: `%LOCALAPPDATA%\Programs\Nectivon`
- Private runtime: `%LOCALAPPDATA%\Programs\Nectivon\runtime\python`
- User root: `%LOCALAPPDATA%\Nectivon`
- Writable children: `config`, `data`, `logs`, `backups`, and `runtime-state`
- Endpoint: `127.0.0.1:8501` only
- Start Menu shortcut: `Nectivon`; it invokes `pythonw.exe` through `launch.vbs`, so no console
  window is shown. A second launch opens the existing UI instead of starting another service.
- Autostart is disabled by default. When the user explicitly enables it, installed mode writes
  only the current-user `Run\Nectivon` value. MSI uninstall removes that value.

The MSI owns only program files, its Start Menu shortcut, and installer registration. Normal
uninstall therefore preserves `%LOCALAPPDATA%\Nectivon`, including knowledge, configuration,
backups, logs, DPAPI fallback material, and Provider credentials. Major upgrades reuse the fixed
UpgradeCode and replace only the program runtime.

## Reproducible runtime

`build.ps1` downloads the official CPython 3.11.9 x64 embeddable distribution and requires its
SHA-256 to equal `009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b`.
It installs the exact dependency closure in `runtime-requirements.txt` into a new
`runtime\python\Lib\site-packages`; it never copies the repository `.venv`. A separate external
CPython 3.11 x64 build interpreter is required only on the packaging machine, and the script
rejects the repository `.venv` as that interpreter.

Before WiX runs, the bundled interpreter must pass the exact `python311._pth`/`sys.path` gate and
import `streamlit`, PyMuPDF, Pillow, RapidOCR, ONNX Runtime, OpenCV, jieba, Pydantic, and
pydantic-settings. The native smoke also exercises PyMuPDF, Pillow, OpenCV, and ONNX Runtime.
The build also downloads Microsoft's signed x64 Visual C++ Redistributable 14.51.36247.0, requires
SHA-256 `843068991daaa1f73ad9f6239bce4d0f6a07a51f18c37ea2a867e9beca71295c`, and uses WiX only to
extract the signed x64 runtime DLLs for app-local deployment beside `python.exe`. This avoids a
machine-wide prerequisite install and UAC on a clean Windows 10/11 machine. Microsoft documents
app-local deployment but recommends central deployment for servicing; redistribution remains
subject to the applicable Visual Studio license terms and must be re-reviewed before public release.
The staged application must then start with this interpreter on `127.0.0.1:8501`, return a healthy
endpoint and home page, render the Nectivon brand under Streamlit's test harness, and stop through
its own PID record. A pre-existing 8501 listener blocks the build; it is never stopped or accepted
as staged evidence.

OCR is complete in the base package (`rapidocr`, `onnxruntime`, OpenCV, Pillow, and their locked
dependencies). `rapidfuzz` is absent because the frozen product has zero runtime imports.

The staging inventory is allowlisted from Git-tracked `app.py`, `pages`, `src`, and Streamlit
configuration plus the installed lifecycle scripts. It rejects `.env`, `.git`, `.venv`, tests,
development caches, user databases, and the excluded repository data/artifact directories.

## Build

```powershell
pwsh -File .\packaging\windows\bootstrap-toolchain.ps1
pwsh -File .\packaging\windows\build.ps1 `
  -BuildPython C:\path\to\independent\python.exe
```

`toolchain.json` pins .NET SDK 10.0.401, its official Windows x64 ZIP SHA-512, WiX 6.0.2, and the
SHA-256 of the Microsoft `dotnet-install.ps1` bootstrap. It also pins pip 26.2.1,
setuptools 84.0.0, and wheel 0.48.0 inside a separate `python-build` environment used only to turn
locked pure-Python sdists into wheels. The bootstrap performs a non-admin, build-only installation under
`%LOCALAPPDATA%\Nectivon-build-tools`; it does not modify the system or user PATH. These tools are not
copied into the MSI or the bundled Python runtime. Generated `build`, `cache`, `dist`, MSI, wheel,
and generated WiX inventory files are ignored by Git.

WiX 6 source remains MS-RL licensed, while the distributed WiX 6 binary package is also subject to
the Open Source Maintenance Fee EULA. Revenue-generating use requires the applicable maintenance
fee. This workflow authorizes only an unsigned internal engineering build; it does not make a
public-release or commercial-distribution compliance determination.

Required build inputs are Windows x64, network access for fixed CPython/dependency inputs, the
isolated toolchain above, and an independent CPython 3.11 x64 with pip. The repository `.venv` is
audit/test-only and is rejected as the build interpreter.

If port 8501 is already in use, the script finishes the bundled-runtime validation but stops before
WiX with:

```text
STAGED_RUNTIME_SMOKE = BLOCKED_PORT_8501_IN_USE
```

Signing is deliberately off by default (`-SigningEnabled $false`). A future signing run must pass
`-SigningEnabled $true -SigningCertificateThumbprint <thumbprint>` and have `signtool.exe`; the
MSI is then signed with SHA-256 plus an RFC 3161 timestamp.

After an unsigned MSI is produced, `inspect-msi.ps1` queries the Windows Installer database without
installing it. It verifies product/version/UpgradeCode/x64/per-user directory and shortcut
contracts and ensures uninstall tables do not target user-owned data. The only allowed custom
action is an uninstall-only, current-user `reg.exe delete` for the exact optional
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Nectivon` value; it cannot delete the Run key
or any Nectivon user data.

## Explicit full deletion and legacy data

Normal uninstall never deletes user data. A separate maintenance command is installed at
`scripts\cleanup_user_data.py`. It requires two confirmations, stops Nectivon, disables autostart,
deletes the four `Nectivon/AI/<provider>` Credential Manager entries, and removes only six exact,
canonical, direct children under `%LOCALAPPDATA%\Nectivon`. It refuses non-allowlisted targets.

`scripts\legacy_data.py <explicit-legacy-data-root>` checks only the supplied registered path and
prints `LEGACY_DATA_FOUND` or `LEGACY_DATA_NOT_FOUND`. It never searches the computer and never
copies or moves data. Migration remains a future explicit user action.
