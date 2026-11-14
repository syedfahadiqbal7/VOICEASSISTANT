# Contributing

1. Create a feature branch; do not commit directly to `main` for routine changes.
2. Keep runtime user data, credentials, logs, virtual environments and generated build folders out of commits.
3. Add regression tests for security-sensitive and reliability-sensitive changes.
4. Run the Python checks from the repository root:

   ```powershell
   python -m pytest -q tests/test_security_reliability_regressions.py
   python tests/phase0_smoke_tests.py
   ```

5. For Flutter changes, run `flutter analyze` and `flutter test` from `voice_assistant_mobile/`.
6. Describe behavior changes, security implications, tests executed and any platform limitations in the pull request.

Never weaken approval, policy, plugin verification or path-boundary controls merely to make a test pass.
