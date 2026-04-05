---
name: open-pr
description: Create a GitHub pull request with structured summary, components, and test plan
argument-hint: [base-branch]
user-invocable: true
allowed-tools: Bash, Read, Grep, Glob, Write
---

Create a GitHub pull request for the current branch using `gh pr create`.

## Steps

1. **Determine base branch**: Use `$ARGUMENTS` as the base branch. If empty, ask the user which branch to target.

2. **Check prerequisites**:
   - Run `git status` — if there are uncommitted changes, warn the user and ask whether to continue.
   - Run `git log <base>..HEAD --oneline` to see all commits being merged.
   - Run `git diff <base>...HEAD --stat` to see changed files summary.
   - Run `git diff <base>...HEAD` to read the actual code changes (truncate if too large).

3. **Analyze changes deeply**: Read the diff output and commit messages carefully. Understand:
   - **What** changed (files, functions, classes)
   - **Why** it changed (purpose, motivation, problem being solved)
   - **How** it changed (approach, technique, patterns used)

4. **Compose the PR body** mentally with this exact structure:

   ```markdown
   ## Summary
   - First bullet explaining the main change and WHY it was needed
   - Additional bullets for other notable changes

   ## Components
   - `path/to/file.py` — Brief description of what changed in this file
   - `path/to/other.py` — Brief description

   ## Test Plan
   - [ ] Describe how to verify the change works
   - [ ] Additional verification steps
   ```

   **Rules for the PR body content:**
   - Write in plain English (not Portuguese, unless the commits are in Portuguese)
   - Summary bullets must explain **why**, not just restate the commit message
   - Components must reference actual changed files with backtick formatting
   - Test Plan must have actionable verification steps as checkboxes

5. **Generate a concise PR title**: Under 72 characters, in imperative mood (e.g., "Add retry logic to API client", "Fix token count in context pruning"). Do NOT just repeat a commit message — synthesize the overall change.

6. **Push the branch** if not already pushed:
   ```
   git push -u origin HEAD
   ```

7. **Create the PR** by piping the body via stdin to avoid temp files:
   ```
   cat <<'EOF' | gh pr create --base <base-branch> --title "<title>" --body-file -
   ## Summary
   - ...

   ## Components
   - ...

   ## Test Plan
   - [ ] ...
   EOF
   ```

   **CRITICAL**: Always pipe the body through stdin with `--body-file -`. This avoids temp files and handles multiline markdown correctly. NEVER use `--body` with inline text. NEVER create temp files.

8. **Return the PR URL** shown in the `gh` output.

## Rules

- ALWAYS pipe the PR body via `cat <<'EOF' | gh pr create ... --body-file -`. NEVER create temp files.
- NEVER use `--body` with inline text or heredoc substitution — it causes escaping issues.
- Title must be under 72 characters, imperative mood, and synthesize the overall change.
- Summary must focus on **why** and **impact**, not just restate file names or commit subjects.
- If the diff is large, focus the summary on the most important changes.
- If there are uncommitted changes, warn the user before proceeding.