# Release process

Releases use GitHub Actions, PyPI Trusted Publishing, and the official MCP Registry. Do not store PyPI API tokens in GitHub.

## One-time configuration

Configure a PyPI pending trusted publisher with:

| Field | Value |
| --- | --- |
| PyPI project | `stremio-mcp-server` |
| GitHub owner | `netixc` |
| Repository | `stremio-mcp` |
| Workflow | `publish.yml` |
| Environment | `pypi` |

The GitHub `pypi` environment restricts deployments to tags matching `v*` and requires a manual confirmation from `netixc`. Because this is a single-maintainer repository, that confirmation is self-approvable; it prevents accidental publication but is not independent authorization review.

## Publish a version

1. Confirm the version matches in `pyproject.toml`, `server.json`, `uv.lock`, the changelog heading, and the pinned `uvx --from stremio-mcp-server==<version>` install example in `README.md`.
2. Replace `Unreleased` in `CHANGELOG.md` with the ISO release date.
3. Run the full local verification documented in `AGENTS.md`.
4. Commit and push the release metadata, then wait for CI to pass.
5. Run the **Release readiness** workflow on `main` and confirm it passes.
6. Create and push the matching tag, for example:

   ```bash
   git tag -a v0.1.0 -m "Release 0.1.0"
   git push origin v0.1.0
   ```

7. Review the protected `pypi` deployment and approve it only if the build job passed for the expected tag and commit.
8. Verify the published package in a clean environment:

   ```bash
   uvx stremio-mcp-server </dev/null
   ```

9. Create the GitHub release from the same tag using the changelog notes.
10. Authenticate `mcp-publisher` with GitHub, publish `server.json`, and verify the Registry listing.

## Failure handling

- Do not reuse a version already uploaded to PyPI; package files are immutable.
- Rerun the same tag only for a transient external failure when the source is unchanged and PyPI accepted no files.
- If repository code or metadata must change, prepare a new version and tag rather than moving the failed tag.
- If PyPI accepted any file or a broken version reached users, leave it immutable, document the problem, and publish a corrected patch version.
- Never move or force-update a release tag.
