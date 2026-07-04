# Purging secrets from git history — instructions and safe checklist

IMPORTANT: Do NOT run history purge without first rotating any secrets you found (AWS keys, GitHub PAT, PagerDuty keys, etc.). History rewrite requires a forced push and will affect all collaborators.

1) Rotate all exposed credentials immediately (revoke and reissue).

2) Preferred: use git-filter-repo (recommended over filter-branch)

Install:

Windows (git-filter-repo requires Python):
```
pip install --user git-filter-repo
```

Command to remove specific files from history:
```
git clone --mirror https://github.com/your/repo.git
cd repo.git
git filter-repo --invert-paths --path .env --path terraform/bootstrap/terraform.tfstate
git push --force
```

3) Alternative: BFG Repo Cleaner (easier for common patterns)

Install BFG (java jar) and run:
```
bfg --delete-files .env
bfg --delete-files "**/*.tfstate"
git reflog expire --expire=now --all
git gc --prune=now --aggressive
git push --force
```

4) Post-rewrite checklist
- Notify the team — everyone must reclone or run `git fetch && git reset --hard origin/main` on affected branches.
- Verify that the offending secrets are gone: `git log --all --full-history -- .env`
- Rotate any remaining tokens if found in backups or elsewhere.

If you want, I can run the non-destructive parts now and prepare an optional script to run the purge once you confirm that credentials are rotated and you're ready to force-push.
