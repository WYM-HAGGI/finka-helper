#!/usr/bin/env python3
"""Build the small, documented file-only state patch against pinned upstream."""
import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

UPSTREAM_SHA256 = "b405e77265fb2d1ba917773a29f284201ebc7ac3c79fc14d5780dd0911be9433"
VERSION = "2.7.0-finka-portable.1"
ROOT = Path(__file__).resolve().parents[1]


def replace_once(path, before, after):
    value = path.read_text()
    if value.count(before) != 1:
        raise RuntimeError("Upstream source does not match pinned patch: " + path.name)
    path.write_text(value.replace(before, after))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-archive", required=True,
                        help="https://github.com/majd/ipatool/archive/refs/tags/v2.7.0.zip")
    parser.add_argument("--go", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cache-dir")
    args = parser.parse_args()
    archive = Path(args.source_archive).resolve()
    if hashlib.sha256(archive.read_bytes()).hexdigest() != UPSTREAM_SHA256:
        raise RuntimeError("Upstream source SHA-256 mismatch")
    work, output = Path(args.work_dir).resolve(), Path(args.output_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    source = work / "ipatool-2.7.0"
    if source.exists():
        raise RuntimeError("Use a new build work directory; existing source will not be overwritten")
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            target = work / item.filename
            if not target.resolve().is_relative_to(work):
                raise RuntimeError("Unsafe source archive path")
        z.extractall(work)
    replace_once(source / "cmd/root.go", 'var version = "dev"',
                 'var version = "dev"\nvar finkaStateDirectory string')
    replace_once(source / "cmd/root.go", "\tcmd.AddCommand(authCmd())",
                 '\tcmd.PersistentFlags().StringVar(&finkaStateDirectory, "finka-state-directory", "", "Finka Helper: absolute private state directory; uses the encrypted file backend only")\n\n\tcmd.AddCommand(authCmd())')
    replace_once(source / "cmd/common.go",
                 "stateDirectory := util.Must(prepareStateDirectory(dependencies.OS, dependencies.Machine.HomeDirectory()))",
                 "stateDirectory := util.Must(resolveFinkaStateDirectory(dependencies.OS, dependencies.Machine.HomeDirectory()))")
    replace_once(source / "cmd/common.go",
                 "func newKeychain(stateDirectory string, interactive bool) keychain.Keychain {\n",
                 """func newKeychain(stateDirectory string, interactive bool) keychain.Keychain {
    backends := []keyring.BackendType{keyring.KeychainBackend, keyring.SecretServiceBackend, keyring.FileBackend}
    if finkaStateDirectory != "" { backends = []keyring.BackendType{keyring.FileBackend} }
""")
    replace_once(source / "cmd/common.go",
                 """AllowedBackends: []keyring.BackendType{
            keyring.KeychainBackend,
            keyring.SecretServiceBackend,
            keyring.FileBackend,
        },""".replace("    ", "\t"),
                 "AllowedBackends: backends,")
    (source / "cmd/finka_portable.go").write_text("""// Finka Helper portability patch. Original authentication/download logic is unchanged.
package cmd
import (
    "fmt"
    "path/filepath"
    "github.com/majd/ipatool/v2/pkg/util/operatingsystem"
)
func resolveFinkaStateDirectory(os operatingsystem.OperatingSystem, home string) (string, error) {
    if finkaStateDirectory == "" { return "", fmt.Errorf("finka-state-directory is required by this portable build") }
    if !filepath.IsAbs(finkaStateDirectory) { return "", fmt.Errorf("private state directory must be absolute") }
    if err := os.MkdirAll(finkaStateDirectory, 0700); err != nil { return "", err }
    return finkaStateDirectory, nil
}
""")
    (source / "cmd/finka_portable_test.go").write_text("""package cmd
import (
    "os"
    "path/filepath"
    "testing"
    "github.com/majd/ipatool/v2/pkg/util/operatingsystem"
)
func TestFinkaPortableState(t *testing.T) {
    base := t.TempDir()
    legacy := filepath.Join(base, ".ipatool")
    if err := os.MkdirAll(legacy, 0700); err != nil { t.Fatal(err) }
    sentinel := filepath.Join(legacy, "unrelated-session")
    if err := os.WriteFile(sentinel, []byte("SYNTHETIC-EXISTING"), 0600); err != nil { t.Fatal(err) }
    finkaStateDirectory = filepath.Join(base, "private")
    keychainPassphrase = "SYNTHETIC-TEST-ONLY"
    defer func(){ finkaStateDirectory = ""; keychainPassphrase = "" }()
    got, err := resolveFinkaStateDirectory(operatingsystem.New(), base)
    if err != nil || got != finkaStateDirectory { t.Fatalf("private state: %v", err) }
    ring := newKeychain(got, false)
    if err := ring.Set("account", []byte("SYNTHETIC-ACCOUNT")); err != nil { t.Fatal(err) }
    value, err := ring.Get("account")
    if err != nil || string(value) != "SYNTHETIC-ACCOUNT" { t.Fatal("file keyring roundtrip failed") }
    encrypted, err := os.ReadFile(filepath.Join(got, "account"))
    if err != nil || string(encrypted) == "SYNTHETIC-ACCOUNT" { t.Fatal("encrypted file backend required") }
    prior, err := os.ReadFile(sentinel)
    if err != nil || string(prior) != "SYNTHETIC-EXISTING" { t.Fatal("legacy data changed") }
    finkaStateDirectory = "relative"
    if _, err := resolveFinkaStateDirectory(operatingsystem.New(), base); err == nil { t.Fatal("accepted relative state") }
}
""")
    cache = Path(args.cache_dir).resolve() if args.cache_dir else work
    env = dict(os.environ, CGO_ENABLED="0", GOTOOLCHAIN="local",
               GOCACHE=str(cache / "go-cache"), GOMODCACHE=str(cache / "go-mod"),
               GOPATH=str(cache / "go-path"))
    go = str(Path(args.go).resolve())
    module = json.loads(subprocess.check_output(
        [go, "mod", "download", "-json", "github.com/byteness/keyring"], cwd=source, env=env))
    keyring = work / "file-keyring"
    keyring.mkdir()
    # Keep upstream's encrypted-file implementation unchanged; compile no OS/1Password backends.
    for name in ("array.go", "config.go", "file.go", "keyring.go", "prompt.go", "tilde.go",
                 "go.mod", "go.sum", "LICENSE"):
        original = Path(module["Dir"]) / name
        if original.is_file():
            shutil.copyfile(original, keyring / name)
    subprocess.run([go, "mod", "edit", "-replace", "github.com/byteness/keyring=../file-keyring"],
                   cwd=source, env=env, check=True)
    subprocess.run([go, "fmt", "./cmd"], cwd=source, env=env, check=True)
    subprocess.run([go, "test", "-mod=mod", "./cmd", "-run", "^TestFinkaPortableState$", "-count=1"],
                   cwd=source, env=env, check=True)
    products = {}
    for platform, target_os, arch in (("macos-arm64", "darwin", "arm64"),
                                     ("macos-x64", "darwin", "amd64"),
                                     ("windows-x64", "windows", "amd64")):
        build_env = dict(env, GOOS=target_os, GOARCH=arch)
        binary = output / ("ipatool-finka-" + platform + (".exe" if target_os == "windows" else ""))
        flags = "-s -w -buildid= -X github.com/majd/ipatool/v2/cmd.version=" + VERSION
        print("Building portable ipatool: " + platform, flush=True)
        subprocess.run([go, "build", "-mod=mod", "-trimpath", "-ldflags", flags, "-o", str(binary), "."],
                       cwd=source, env=build_env, check=True)
        archive_path = output / ("ipatool-finka-" + platform + ".tar.gz")
        notice = ("ipatool v2.7.0 by Majd Alfhaily, MIT licensed.\n"
                  "Modified by Finka Helper contributors: explicit private state and encrypted file backend.\n"
                  "Patch/build source: https://github.com/WYM-HAGGI/finka-helper/tree/v1.1.0/tools\n").encode()
        members = [(binary.name, binary.read_bytes(), 0o755),
                   ("LICENSE", (source / "LICENSE").read_bytes(), 0o644),
                   ("KEYRING_LICENSE", (keyring / "LICENSE").read_bytes(), 0o644),
                   ("FINKA_MODIFICATIONS.txt", notice, 0o644)]
        with archive_path.open("wb") as out, gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
            for name, content, mode in members:
                item = tarfile.TarInfo(name)
                item.size, item.mode, item.mtime = len(content), mode, 0
                tar.addfile(item, io.BytesIO(content))
        products[platform] = {"url": "https://github.com/WYM-HAGGI/finka-helper/releases/download/v1.1.0/" + archive_path.name,
                              "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                              "size": archive_path.stat().st_size, "version": VERSION}
    (output / "portable-ipatool.json").write_text(json.dumps(products, indent=2) + "\n")


if __name__ == "__main__":
    main()
