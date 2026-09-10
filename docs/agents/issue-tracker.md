# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues. Use the `gh` CLI for all
operations. Issue titles and bodies are written in English.

## Repository

Infer the repository from `git remote -v`. The configured remote is:

`https://github.com/ignazhabibi/vi_api_client.git`

## Conventions

- Create issues with `gh issue create`.
- Read issues and their comments with `gh issue view <number> --comments`.
- List issues with `gh issue list` and request labels, bodies, and comments as
  JSON when required.
- Comment with `gh issue comment`.
- Apply or remove labels with `gh issue edit`.
- Close issues with `gh issue close`.

## Pull requests as a triage surface

PRs as a request surface: no.

GitHub shares one number space across issues and pull requests. When a reference
is ambiguous, resolve it as a pull request first and fall back to an issue.

## Skill operations

When a skill says "publish to the issue tracker," create a GitHub issue.

When a skill says "fetch the relevant ticket," read the complete issue body,
comments, and labels.

Use GitHub native issue dependencies for blocking relationships when available.
Otherwise, record blockers in the issue body as:

`Blocked by: #<number>, #<number>`
