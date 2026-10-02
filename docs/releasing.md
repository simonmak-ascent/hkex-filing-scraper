# How to Make a Release

A release is just a **tag**. You pick a number, push the tag, and a robot does the rest.

You do not edit any files. The version number comes from the tag.

## Step 1 — Pick a number

| What changed        | New version | Example            |
| ------------------- | ----------- | ------------------ |
| Small fix           | `vX.Y.(Z+1)` | `v1.1.0` → `v1.1.1` |
| New feature         | `vX.(Y+1).0` | `v1.1.0` → `v1.2.0` |
| Big / breaking change | `v(X+1).0.0` | `v1.1.0` → `v2.0.0` |

Always start the tag with `v`. Use three numbers separated by dots.

### Pre-release (test) versions

For a release candidate or a test, add a suffix: `v1.2.0-rc1`, `v1.2.0-beta1`. These are
marked as **pre-releases** on GitHub and never become the "Latest" release. Use one when
you want to try the process without publishing a real version.

## Step 2 — Type two commands

In the project folder, on the `main` branch, with your work already pushed:

```bash
git tag -a v1.2.0 -m "v1.2.0"
git push origin v1.2.0
```

Change `v1.2.0` to the number you picked. That's it.

## Step 3 — Wait about two minutes

1. Open the repository on GitHub.
2. Click the **Actions** tab.
3. Find the run named **Release** (and **PyPI**, which publishes the same build to PyPI).
   A green tick means it worked.

When the **Release** run is done, open the **Releases** page. Your new release has four files:

- `hkex_filing_scraper-<version>-py3-none-any.whl`
- `hkex_filing_scraper-<version>.tar.gz`
- `hkex-filing-scraper-<version>.sbom.cdx.json`
- `SHA256SUMS`

When the **PyPI** run is done, the same wheel and sdist are live at
<https://pypi.org/project/hkex-filing-scraper/>. Publishing uses PyPI **Trusted
Publishing** (OpenID Connect), so there is no long-lived upload token to store or rotate.

## Step 4 — Anyone can now install it

```bash
pip install hkex-filing-scraper
```

Or pin a specific release, or install straight from the Git tag:

```bash
pip install "git+https://github.com/simonplmak-cloud/hkex-filing-scraper@v1.2.0"
```

You can also download the `.whl` from the Releases page and install that.

## If something goes wrong

The **Actions** tab shows a red ❌. Click the failed step to read the error, fix it on
`main`, and push. Then remove the release and the tag, and try again:

```bash
# Deletes the GitHub Release AND its tag in one command:
gh release delete v1.2.0 --yes --cleanup-tag

# If you also created the tag locally:
git tag -d v1.2.0
# fix the problem, push to main, then repeat Step 2
```

> Deleting a tag on its own does **not** delete its GitHub Release — use
> `gh release delete <tag> --yes --cleanup-tag` (or the "Delete" button on the
> Releases page) to remove both.

If the robot is badly broken and you need the files **right now**, there is a manual
escape hatch: see [Release automation → Manual fallback](release-automation.md#8-manual-fallback).

## Verifying a release

Every release is built by CI and carries a signed attestation of how it was built, plus a
CycloneDX SBOM. Consumers can check both:

```bash
# Build provenance: was this wheel built by this repository's release workflow?
gh attestation verify hkex_filing_scraper-2.0.0-py3-none-any.whl \
  --repo simonplmak-cloud/hkex-filing-scraper

# The SBOM that ships alongside it
gh attestation verify hkex_filing_scraper-2.0.0-py3-none-any.whl \
  --repo simonplmak-cloud/hkex-filing-scraper \
  --predicate-type https://cyclonedx.org/bom
```

`SHA256SUMS` in the release assets lets you check the download itself:

```bash
sha256sum --check SHA256SUMS
```

## Rolling back a release

If a published version turns out to be bad, **do not delete the tag or the release** — other
people may already be pinned to it. Instead:

1. **Consumers roll back by pin:** install the previous good tag or release wheel.

   ```bash
   pip install "git+https://github.com/simonplmak-cloud/hkex-filing-scraper@v1.1.0"
   # or download the .whl from the previous Release
   ```

2. **Cut a fix-forward version** (e.g. `v2.0.1`) from `main`.
3. **Mark the bad release** in its notes ("superseded by v2.0.1 — do not use") rather than
   removing it, so the audit trail stays intact.

Rollback target time: **< 5 minutes** (pin the previous tag). This is why the version comes
from the tag: any commit is installable, any release is reproducible.

## Want to understand how it works?

Read [docs/release-automation.md](release-automation.md). It explains every step, what can
break, and how to fix it — written so a future maintainer can repair it years later.
