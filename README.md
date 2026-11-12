# docdrift

Find configuration options that exist in the code but are missing from the docs.

Go projects define user-facing options in three places: struct tags (`keda:"name=..."`, `mapstructure:"..."`, `json:"..."`), CLI flags (`cmd.Flags().StringVar(..., "name", ...)`) and Kubernetes manifests that read ConfigMap keys (`configMapKeyRef: key: ...`). Docs drift away from all three. `docdrift` extracts the option names from the source and reports every name that never appears in the docs you point it at.

One Python file, no dependencies, exit code 1 when something is missing so it can gate CI.

## What it found

I wrote this while contributing to CNCF projects; every run below turned into a merged-or-open upstream change:

| Project | Command | Result |
| --- | --- | --- |
| KEDA | `tags --tag keda pkg/scalers/solace_dm_scaler.go` vs the scaler page | 4 `activation*` parameters that the code accepts but nothing reads ([kedacore/keda#8359](https://github.com/kedacore/keda/issues/8359)) |
| KEDA | `tags --tag keda pkg/scalers/` vs `content/docs/2.22/scalers/` | undocumented scaler parameters across MongoDB, NATS JetStream, Solace DM ([keda-docs#1938](https://github.com/kedacore/keda-docs/pull/1938), [#1939](https://github.com/kedacore/keda-docs/pull/1939)) |
| Kyverno | `flags cmd/internal/flag.go` vs the customization page | 13 container flags missing from the flags table ([kyverno/website#2274](https://github.com/kyverno/website/pull/2274)) |
| Argo CD | `cmkeys manifests/base --configmap argocd-cmd-params-cm` vs the reference ConfigMap | 15 keys wired into deployments but absent from the documented ConfigMap ([argo-cd#30933](https://github.com/argoproj/argo-cd/pull/30933)) |
| OpenTelemetry Collector | `tags --tag mapstructure receiver/statsdreceiver/config.go` vs its README | `counter_type` undocumented ([contrib#53015](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/53015)), same for three other components |

## Usage

```
python docdrift.py tags   [--tag keda|mapstructure|json] <go files or dirs>  --docs <docs files or dirs>
python docdrift.py flags  <go files or dirs>                                   --docs <docs files or dirs>
python docdrift.py cmkeys [--configmap NAME] <manifest files or dirs>          --docs <docs files or dirs>
```

Examples:

```bash
# KEDA: scaler parameters in code vs the scaler docs page
python docdrift.py tags --tag keda keda/pkg/scalers/mongo_scaler.go \
  --docs keda-docs/content/docs/2.22/scalers/mongodb.md

# Kyverno: container flags vs the flags reference
python docdrift.py flags kyverno/cmd/internal/flag.go \
  --docs website/src/content/docs/docs/installation/customization.md

# Argo CD: ConfigMap keys consumed by the deployments vs the reference ConfigMap
python docdrift.py cmkeys argo-cd/manifests/base --configmap argocd-cmd-params-cm \
  --docs argo-cd/docs/operator-manual/argocd-cmd-params-cm.yaml

# OpenTelemetry Collector: a component's config struct vs its README
python docdrift.py tags --tag mapstructure receiver/statsdreceiver/config.go \
  --docs receiver/statsdreceiver/README.md
```

Sample output:

```
15 options found in code, 4 missing from docs
  MISSING  activationAggregatedClientAverageTxByteRateTarget    (pkg/scalers/solace_dm_scaler.go)
  MISSING  activationAggregatedClientAverageTxMsgRateTarget    (pkg/scalers/solace_dm_scaler.go)
  MISSING  activationAggregatedClientTxByteRateTarget    (pkg/scalers/solace_dm_scaler.go)
  MISSING  activationAggregatedClientTxMsgRateTarget    (pkg/scalers/solace_dm_scaler.go)
```

Add `--all` to also list the options that are documented.

## How the matching works

An option counts as documented when its exact name appears as a whole token anywhere in the docs set (Markdown, YAML, text, HTML). That is deliberately simple: it produces a short list of candidates for a human to check, not a verdict. Every hit still needs a look at the code to confirm the option is real, reachable and worth documenting; in the Solace DM case the right outcome was a bug report, not a docs line.

Known gaps: options built dynamically (`fmt.Sprintf` names, loops over a list), flags registered through helper functions with non-standard signatures, and tags on fields of embedded structs that the project documents under a different prefix. `--include-tests` scans `_test.go` files too, which is usually noise.

## Tests

```
python -m unittest discover -s tests -v
```

## License

MIT
