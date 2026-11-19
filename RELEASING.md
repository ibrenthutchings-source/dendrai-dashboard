# Releasing

Versioning is manual semver (`MAJOR.MINOR.PATCH`), tracked in the root
`package.json`. There's no commit-message-driven automation — pushing a
`vX.Y.Z` tag is what triggers a release.

To cut a release:

```sh
npm version patch   # or: minor / major
git push --follow-tags
```

`npm version` bumps `package.json`/`package-lock.json`, commits the bump,
and creates a local `vX.Y.Z` tag. Pushing that tag runs
[`.github/workflows/release.yml`](.github/workflows/release.yml), which
publishes a GitHub Release with auto-generated notes (commits since the
previous tag).

This repo is private with an `UNLICENSED`/proprietary [LICENSE](LICENSE) —
releases are for internal version tracking, not public distribution.
