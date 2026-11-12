import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import docdrift  # noqa: E402


def write(dirpath, name, content):
    path = os.path.join(dirpath, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


class TagsTest(unittest.TestCase):
    def test_keda_and_mapstructure_tags(self):
        with tempfile.TemporaryDirectory() as d:
            src = write(d, "scaler.go", '''
package scalers
type cfg struct {
    Host     string `keda:"name=host, order=triggerMetadata"`
    Ratio    int    `keda:"name=activationRatio, order=triggerMetadata, default=0"`
    Interval string `mapstructure:"interval,omitempty"`
    Embedded `mapstructure:",squash"`
    Ignored  string `json:"-"`
}
''')
            write(d, "scaler_test.go", 'package scalers\ntype t struct { X string `keda:"name=onlyInTests"` }\n')
            keda = docdrift.extract_tags([src], "keda", skip_tests=True)
            self.assertEqual(set(keda), {"host", "activationRatio"})
            ms = docdrift.extract_tags([d], "mapstructure", skip_tests=True)
            self.assertEqual(set(ms), {"interval"})
            with_tests = docdrift.extract_tags([d], "keda", skip_tests=False)
            self.assertIn("onlyInTests", with_tests)


class FlagsTest(unittest.TestCase):
    def test_cobra_and_stdlib_flags(self):
        with tempfile.TemporaryDirectory() as d:
            src = write(d, "flags.go", '''
package main
func setup() {
    cmd.Flags().StringVar(&o.Name, "service-name", "", "x")
    cmd.PersistentFlags().BoolP("verbose", "v", false, "x")
    fs.DurationVar(&o.Interval, "reconcile-interval", 10*time.Second, "x")
    flag.Int("port", 8080, "x")
    flag.StringVar(&kubeconfig, "kubeconfig", "", "x")
    flagset := flag.NewFlagSet("openreports", flag.ExitOnError)
    flagset.BoolVar(&enabled, "openreportsEnabled", false, "x")
}
''')
            names = docdrift.extract_flags([src], skip_tests=True)
            # "openreports" names a flag set, not a flag, and must not be reported
            self.assertEqual(set(names), {"service-name", "verbose", "port", "kubeconfig", "openreportsEnabled"})


class CMKeysTest(unittest.TestCase):
    MANIFEST = '''
env:
  - name: A
    valueFrom:
      configMapKeyRef:
        name: argocd-cmd-params-cm
        key: reposerver.grpc.max.size
        optional: true
  - name: B
    valueFrom:
      configMapKeyRef:
        key: controller.status.processors
        name: argocd-cmd-params-cm
  - name: C
    valueFrom:
      configMapKeyRef:
        name: other-cm
        key: unrelated.key
'''

    def test_keys_and_configmap_filter(self):
        with tempfile.TemporaryDirectory() as d:
            m = write(d, "deploy.yaml", self.MANIFEST)
            all_keys = docdrift.extract_cmkeys([m], None)
            self.assertEqual(set(all_keys), {"reposerver.grpc.max.size", "controller.status.processors", "unrelated.key"})
            only = docdrift.extract_cmkeys([m], "argocd-cmd-params-cm")
            self.assertEqual(set(only), {"reposerver.grpc.max.size", "controller.status.processors"})


class MatchingTest(unittest.TestCase):
    def test_whole_token_match(self):
        docs = "Set `reposerver.grpc.max.size` in the ConfigMap. The `port` flag defaults to 8080."
        names = {"reposerver.grpc.max.size": set(), "port": set(), "reposerver.grpc": set(), "ports": set()}
        missing = docdrift.missing_in_docs(names, docs)
        # "reposerver.grpc" is only a prefix of a documented key and "ports" is not "port"
        self.assertEqual(missing, ["ports", "reposerver.grpc"])

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            src = write(d, "x.go", 'package p\ntype c struct { A string `mapstructure:"documented"`; B string `mapstructure:"forgotten"` }\n')
            doc = write(d, "README.md", "The `documented` option exists.\n")
            self.assertEqual(docdrift.main(["tags", src, "--docs", doc]), 1)
            doc_ok = write(d, "README2.md", "`documented` and `forgotten` are both here.\n")
            self.assertEqual(docdrift.main(["tags", src, "--docs", doc_ok]), 0)


if __name__ == "__main__":
    unittest.main()
