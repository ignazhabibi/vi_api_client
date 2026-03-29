# Consumer Repository Protection

## Critical Rule

I am strictly forbidden from silently modifying files in separate consumer
repositories, such as `vi_climate_devices`, as part of the normal
`vi_api_client` workflow.

Even though the same owner may maintain multiple related repositories, consumer
repos are separate codebases with their own:

- versioning and dependency update flow
- test suites and release cadence
- review history and rollout constraints

## My Responsibility

When I identify a required consumer-repo follow-up, I must:

1. Stop before making edits outside this repository.
2. Inform the user what needs to change and why.
3. List the likely consumer files or surfaces affected.
4. Call out compatibility impact and any expected version bump or dependency
   update.
5. Wait for the user to explicitly request separate multi-repo work before
   editing another repository.

## Examples

### Correct Approach

1. I identify that a renamed CLI flag or model field in `vi_api_client` will
   require a consumer update.
2. I explain which downstream docs, code paths, or dependency pins likely need
   adjustment.
3. The user decides whether to handle the consumer repo separately or ask for a
   coordinated follow-up task.

### Wrong Approach

- I directly edit `vi_climate_devices` while supposedly working only in this
  repository.
- I make consumer-specific hacks in the library without documenting the public
  contract impact.
- I treat downstream breakage as acceptable without surfacing the migration
  impact.

## Scope

- Library files in this repository: I can modify them.
- Separate consumer repositories: I must not modify them unless the user asks
  for explicit cross-repo work.

## Key Principle

"Inform, then coordinate." Library work may imply downstream changes, but it
does not silently include them.
