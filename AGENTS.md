# devin-bridge

- When you open or take over a GitHub PR in a repo that has `.github/workflows/devin-bridge.yml`,
  follow the `/devin-bridge:pr-loop` skill: link this session to the PR, then work through CI,
  review threads and Devin Review findings until the PR is clean.
- Messages that start "New review / review comment / comment on https://github.com/..." are PR
  feedback forwarded by the bridge. Handle them with the same skill.
- Never merge a PR unless the user says so. Never put a Devin or GitHub key in a PR, comment or log.
