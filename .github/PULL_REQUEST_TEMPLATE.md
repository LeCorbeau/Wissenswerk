## Summary

-

## Verification

- [ ] `python3 -m py_compile wissenswerk.py`
- [ ] `./wissenswerk.py doctor --json`
- [ ] `./wissenswerk.py export plan --strict --json`
- [ ] `./wissenswerk.py test --json`
- [ ] `git diff --check`

## Public Export Impact

- [ ] No private corpora, generated reports, secrets, or tenant-specific content are added to the public export.
- [ ] Documentation and `wissenswerk_export_manifest.json` are updated when public surfaces change.
