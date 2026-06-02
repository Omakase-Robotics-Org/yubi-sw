# Contributing to YUBI

Thanks for considering a contribution! YUBI is in its early days (pre-1.0), and bug reports, fixes, and well-scoped improvements are all welcome.

## Code of Conduct

This project adheres to the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you agree to uphold it. To report concerns privately, contact the maintainers at `report@airoa.org`.

## Reporting issues

- **Bugs**: open a GitHub issue with a minimal reproducible example (the variant config, device setup, or bringup command that triggers it), the YUBI version (git hash), and your environment (OS, ROS 2 distro, hardware).
- **Security issues**: please report privately to `report@airoa.org` rather than opening a public issue.
- **Feature ideas**: open an issue to discuss before sending a large PR. Small fixes can go straight to a PR.

## Finding ways to help

Issues labeled [`good first issue`](https://github.com/airoa-org/yubi-sw/labels/good%20first%20issue) and [`help wanted`](https://github.com/airoa-org/yubi-sw/labels/help%20wanted) are good starting points.

YUBI is pre-1.0 with a deliberately narrow public surface. Before opening a PR for new functionality, please open an issue to discuss it — we may decide the feature is out of scope or needs a different shape.

## Development setup

### Clone

```bash
git clone https://github.com/airoa-org/yubi-sw.git --recursive
cd yubi-sw
```

The repository uses git submodules; `--recursive` is required.

### Docker (recommended)

The dev container has the toolchains preinstalled. See [README.md](README.md)
for host setup (udev rules, encoder calibration) and the full bringup flow.

## Building and testing

```bash
make help          # Show available targets
make lint          # ruff lint + format check
make test          # Unit tests with pytest
make test-config   # build_runtime_configs / variant tests
make build-config  # Regenerate config/_runtime/<variant>/
make docker        # Build all Docker images (yubi + yubi-core)
```

## Code style

- **Python**: `ruff` for linting and formatting. Run `make lint` before opening a PR.

## Pull requests

- Branch from `main`.
- Run the relevant tests and lints locally before opening the PR.
- Write a clear PR description: what changed, why, and a brief test plan.
- Commits in this repository follow [Conventional Commits](https://www.conventionalcommits.org/) (e.g. `feat:`, `fix:`, `docs:`, `chore:`, `ci:`).
- Keep changes focused — unrelated changes are easier to review as separate PRs.
- All contributions are licensed under [Apache-2.0](LICENSE).

## Use of AI

Contributors may use a variety of tools when preparing changes to YUBI, including AI systems (e.g. large language models or coding assistants). Contributors using such systems are expected to follow these principles:

- Regardless of how a change is produced, the individual submitting the pull request is considered the **author** of the contribution and is fully **responsible** for it.
- The pull request author **must understand the implementation end-to-end** and be able to **explain and justify the design and code** during review.
- Tools, including AI systems, **are not** considered contributors. **Responsibility and authorship remain with the human** submitting the change.
- Contributors are **encouraged to disclose** significant AI assistance in the pull request description for transparency.
- AI-generated code must be tested in your own environment — do not submit code for a robot platform or hardware path that you cannot run locally.

## Need help?

If you get stuck or want to discuss before starting, please open an issue or start a [GitHub Discussion](https://github.com/airoa-org/yubi-sw/discussions).

---

Thank you for contributing to YUBI! 🤖
