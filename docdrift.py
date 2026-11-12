#!/usr/bin/env python3
"""docdrift: find configuration options that exist in code but are missing from the docs.

Three extractors cover the places where Go projects define user-facing options:

  tags    struct field tags such as `keda:"name=foo"`, `mapstructure:"foo"`, `json:"foo"`
  flags   cobra/pflag flag definitions such as cmd.Flags().StringVar(&x, "foo", ...)
  cmkeys  Kubernetes manifests that read a ConfigMap key via configMapKeyRef (key: foo)

Each extractor produces option names. docdrift then searches a docs directory
(Markdown, YAML, plain text) for every name and prints the ones that never appear.

Examples:

  # KEDA: scaler parameters defined in code vs the scaler docs page
  docdrift tags --tag keda keda/pkg/scalers/mongo_scaler.go --docs keda-docs/content/docs/2.22/scalers/mongodb.md

  # Kyverno: container flags vs the flags reference page
  docdrift flags kyverno/cmd/internal/flag.go --docs website/content/en/docs/installation/customization.md

  # Argo CD: ConfigMap keys wired into deployments vs the reference ConfigMap
  docdrift cmkeys argo-cd/manifests/base --docs argo-cd/docs/operator-manual/argocd-cmd-params-cm.yaml

  # OpenTelemetry Collector: component config fields vs the component README
  docdrift tags --tag mapstructure receiver/statsdreceiver/config.go --docs receiver/statsdreceiver/README.md

Exit code is 1 when something is missing, so it can run in CI.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterable

GO_EXT = (".go",)
MANIFEST_EXT = (".yaml", ".yml")
DOC_EXT = (".md", ".markdown", ".yaml", ".yml", ".txt", ".rst", ".html")


def iter_files(paths: Iterable[str], exts: tuple[str, ...]) -> Iterable[str]:
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for root, _dirs, files in os.walk(p):
            for f in files:
                if f.endswith(exts):
                    yield os.path.join(root, f)


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="ignore") as fh:
        return fh.read()


# --- extractors -----------------------------------------------------------

def extract_tags(paths: list[str], tag: str, skip_tests: bool) -> dict[str, set[str]]:
    """Return {option_name: {files}} for struct tags of the given key.

    Handles `keda:"name=foo, order=..."`, `mapstructure:"foo,omitempty"`, `json:"foo"`.
    """
    found: dict[str, set[str]] = {}
    pattern = re.compile(r'`[^`]*\b' + re.escape(tag) + r':"([^"]*)"')
    for f in iter_files(paths, GO_EXT):
        if skip_tests and f.endswith("_test.go"):
            continue
        for m in pattern.finditer(read(f)):
            raw = m.group(1).strip()
            name = raw
            if "=" in raw:  # keda style: name=foo, order=triggerMetadata
                kv = dict(part.strip().split("=", 1) for part in raw.split(",") if "=" in part)
                name = kv.get("name", "")
            else:  # mapstructure/json style: foo,omitempty  or  ,squash
                name = raw.split(",", 1)[0].strip()
            if not name or name == "-":
                continue
            found.setdefault(name, set()).add(f)
    return found


# Flag definitions in stdlib flag, pflag and cobra all look like
#   <receiver>.<Type>[Var][P]("name", ...)  or  <receiver>.<Type>Var(&target, "name", ...)
# where <receiver> is `flag`, a flag set variable or `cmd.Flags()`. Matching on the typed
# method name keeps constructors such as NewFlagSet("name", ...) out of the results.
FLAG_TYPES = (
    "Bool", "String", "Int", "Int8", "Int16", "Int32", "Int64", "Uint", "Uint8", "Uint16", "Uint32",
    "Uint64", "Float32", "Float64", "Duration", "Count", "IP", "IPMask", "IPNet", "BytesHex",
    "StringSlice", "StringArray", "StringToString", "StringToInt", "IntSlice", "Int32Slice",
    "Int64Slice", "UintSlice", "Float32Slice", "Float64Slice", "BoolSlice", "DurationSlice",
)
# The receiver must look like a flag set (`flag`, `pflag`, `fs`, `msfs`, `flagset`, `cmd.Flags()`,
# `cmd.PersistentFlags()`), otherwise attribute.String("key", v) or zap.String("err", e) would
# be reported as flags.
FLAG_RECEIVER = r"(?:\b(?:[A-Za-z_]\w*\.)*(?:[A-Za-z_]*[Ff]lags?[A-Za-z_]*(?:\(\))?|[a-z]*fs))"
FLAG_PATTERN = re.compile(
    FLAG_RECEIVER + r"\.(?:" + "|".join(FLAG_TYPES) + r")(?:Var)?P?\(\s*(?:&[\w.\[\]()]+\s*,\s*)?\"([A-Za-z0-9][\w.-]*)\""
)
# generic Var(value, "name", usage) / VarP(value, "name", "n", usage)
FLAG_PATTERN_VAR = re.compile(FLAG_RECEIVER + r'\.VarP?\(\s*[^,()]+(?:\([^)]*\))?\s*,\s*"([A-Za-z0-9][\w.-]*)"')


def extract_flags(paths: list[str], skip_tests: bool) -> dict[str, set[str]]:
    """Return {flag_name: {files}} for cobra/pflag and stdlib flag definitions."""
    found: dict[str, set[str]] = {}
    for f in iter_files(paths, GO_EXT):
        if skip_tests and f.endswith("_test.go"):
            continue
        text = read(f)
        for pat in (FLAG_PATTERN, FLAG_PATTERN_VAR):
            for m in pat.finditer(text):
                found.setdefault(m.group(1), set()).add(f)
    return found


CMKEY_PATTERN = re.compile(r"configMapKeyRef:\s*\n(?:[ \t]+\w+:.*\n)*?[ \t]+key:\s*([\w.\-/]+)")


def extract_cmkeys(paths: list[str], name_filter: str | None) -> dict[str, set[str]]:
    """Return {configmap_key: {files}} for every configMapKeyRef in the manifests."""
    found: dict[str, set[str]] = {}
    for f in iter_files(paths, MANIFEST_EXT):
        text = read(f)
        for m in re.finditer(r"configMapKeyRef:\s*\n((?:[ \t]+\w+:.*\n)+)", text):
            block = m.group(1)
            key = re.search(r"^[ \t]+key:\s*([\w.\-/]+)", block, re.M)
            cm = re.search(r"^[ \t]+name:\s*([\w.\-]+)", block, re.M)
            if not key:
                continue
            if name_filter and (not cm or cm.group(1) != name_filter):
                continue
            found.setdefault(key.group(1), set()).add(f)
    return found


# --- docs search ----------------------------------------------------------

def docs_text(paths: list[str]) -> str:
    return "\n".join(read(f) for f in iter_files(paths, DOC_EXT))


def missing_in_docs(names: dict[str, set[str]], docs: str, flags: bool = False) -> list[str]:
    """An option counts as documented when its exact name appears as a whole token.

    A name followed by a dotted continuation (``reposerver.grpc`` inside
    ``reposerver.grpc.max.size``) is only a prefix, not a match; a trailing
    sentence period (``... the port.``) still counts. A name preceded by a dot
    (``metricsUtilization.metricsServer``) is a nested field path and counts.
    For flags, ``--name`` also counts, since that is how docs spell them.
    """
    out = []
    for name in sorted(names):
        escaped = re.escape(name)
        found = re.search(r"(?<![\w-])" + escaped + r"(?![\w-])(?!\.\w)", docs)
        if not found and flags:
            found = re.search(r"--" + escaped + r"(?![\w-])", docs)
        if not found:
            out.append(name)
    return out


# --- cli ------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="docdrift", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("paths", nargs="+", help="source files or directories to scan")
    common.add_argument("--docs", nargs="+", required=True, help="docs files or directories to search")
    common.add_argument("--all", action="store_true", help="also print the options that are documented")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("tags", parents=[common], help="struct tags in Go files")
    t.add_argument("--tag", default="mapstructure", help="tag key, e.g. keda, mapstructure, json (default mapstructure)")
    t.add_argument("--include-tests", action="store_true")

    fl = sub.add_parser("flags", parents=[common], help="cobra/pflag/flag definitions in Go files")
    fl.add_argument("--include-tests", action="store_true")

    ck = sub.add_parser("cmkeys", parents=[common], help="configMapKeyRef keys in Kubernetes manifests")
    ck.add_argument("--configmap", help="only keys read from this ConfigMap name")

    args = ap.parse_args(argv)

    if args.cmd == "tags":
        names = extract_tags(args.paths, args.tag, not args.include_tests)
    elif args.cmd == "flags":
        names = extract_flags(args.paths, not args.include_tests)
    else:
        names = extract_cmkeys(args.paths, args.configmap)

    docs = docs_text(args.docs)
    missing = missing_in_docs(names, docs, flags=(args.cmd == "flags"))

    print(f"{len(names)} options found in code, {len(missing)} missing from docs")
    for name in missing:
        files = sorted(os.path.normpath(f).replace(os.sep, "/") for f in names[name])
        print(f"  MISSING  {name}    ({', '.join(files[:2])}{', ...' if len(files) > 2 else ''})")
    if args.all:
        for name in sorted(names):
            if name not in missing:
                print(f"  ok       {name}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
