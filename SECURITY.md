# Security policy

## Supported versions

Security fixes are applied to the latest code on the `main` branch. Older commits and forks may not receive fixes.

## Report a vulnerability

Please do not disclose a vulnerability in a public issue.

Use GitHub's **Report a vulnerability** option in the repository's Security tab when it is available. If private vulnerability reporting is not available, open a minimal issue asking the maintainer for a private contact method without including exploit details or sensitive data.

Include:

- A description of the affected behavior and impact
- Reproduction steps or a minimal proof of concept
- The affected commit or version
- Suggested mitigations, if known

Do **not** include real TMDB keys, Stremio auth keys, `.env` files, MCP client configuration, account data, device IPs, ADB private keys, or unredacted logs. Use placeholders and the smallest safe reproduction.

No fixed response timeline is promised, but reports will be assessed as maintainer availability permits. Please allow time for a fix before public disclosure.

## Security boundaries

This server crosses several sensitive boundaries:

- Native ADB can control an authorized Android TV.
- `STREMIO_AUTH_KEY` permits Stremio library reads and writes.
- `library add/remove` changes account data.
- `play` and `tv_control` send commands to a physical device.
- TMDB and Stremio operations send requests to external services.

Users should keep credentials in ignored local files or private client configuration, review mutating tool calls, protect `~/.android/adbkey`, and disable Wireless Debugging when it is not needed.
