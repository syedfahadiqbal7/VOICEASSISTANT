# GitHub publishing and CI/CD

Target repository: https://github.com/syedfahadiqbal7/VOICEASSISTANT
Default branch: `main`

## First-time source synchronization

The source package is prepared with the full desktop application, plugins, tests, Flutter source, dependency manifests, GitHub Actions workflows and privacy-safe defaults. The connected GitHub service can inspect and manage repository metadata, but this execution environment cannot transfer the complete local directory as a Git push. The repository owner must run the included publishing script once from Windows.

1. Download and extract `VOICEASSISTANT_GitHub_Ready.zip`.
2. Install Git for Windows and authenticate with GitHub (Git Credential Manager or `gh auth login`).
3. Configure your Git author identity if not already configured:

   ```powershell
   git config --global user.name "Your Name"
   git config --global user.email "you@example.com"
   ```

4. Open PowerShell in the extracted project root and run:

   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\Publish-To-GitHub.ps1
   ```

The script clones the existing `main` branch if present, or initializes it if the repository is empty. It copies the source while excluding local runtime history, exports, backups, logs, IDE files, secrets and build outputs. It commits and pushes without force-pushing. If branch protection blocks direct pushes, create a feature branch and open a pull request instead.

## Continuous integration

`.github/workflows/ci.yml` runs automatically on pushes to `main`, pull requests targeting `main`, version tags and manual dispatch:

- Windows + Python 3.11: install `requirements-dev.txt`, compile Python sources, run the security/reliability pytest suite and Phase 0 smoke tests.
- Ubuntu + Flutter stable: resolve Flutter packages, run `flutter analyze`, run `flutter test`, and build a release Android APK.

## Continuous delivery / releases

After CI is green, publish a semantic version tag, for example:

```powershell
git tag v1.0.0
git push origin v1.0.0
```

The workflow builds a source ZIP and Android release APK, then creates a GitHub Release and attaches both artifacts. This is artifact-based delivery; it does not deploy a hosted backend or publish to Google Play/App Store.

## GitHub repository settings recommended

- Keep `main` protected; require pull requests and passing CI checks before merge.
- Enable Dependabot alerts and security updates.
- Enable private vulnerability reporting if available.
- Do not add secrets to repository files. Configure any future deployment secrets under GitHub Actions secrets/environments with least privilege.
