# Security Policy

## Supported Versions

`pingtester` is a small, single-file hobby tool with no versioned releases.
Security fixes are applied to the `main` branch, and only the latest commit on
`main` is supported. If you are running an older checkout, please pull the
latest `main` before reporting an issue.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security-sensitive reports.

Instead, report privately via one of:

- GitHub's [private vulnerability reporting](https://github.com/dansity/pingtester/security/advisories/new)
  ("Report a vulnerability" under the repository's **Security** tab), or
- A direct message to the maintainer ([@dansity](https://github.com/dansity)).

When reporting, please include:

- A description of the issue and its potential impact
- Steps to reproduce (command line, host, mode, and terminal if relevant)
- The affected file(s) and, if known, a suggested fix

You can expect an acknowledgement within a few days. Because this is a
volunteer-maintained project, please allow reasonable time for a fix before any
public disclosure.

## Scope and Threat Model

`pingtester` is a local, interactive terminal tool. It has **no telemetry, no
analytics, and no update mechanism**, and it stores nothing remotely. Keep this
in mind when assessing impact:

- **`pingtester.py`** only contacts the target host you choose:
  - **ICMP mode** shells out to the system `ping` utility.
  - **TCP mode** opens a socket to the target host/port.
  - **HTTP(S) mode** issues a GET request and reads the first byte.
    **TLS certificate validation is intentionally disabled** in this mode
    because it measures latency, not trust — do not rely on HTTP mode to
    authenticate a server.
  - **Traceroute mode** shells out to the system `traceroute`/`tracepath`.
- **`report.py`** makes no network calls. It reads your local CSV logs and
  writes a self-contained HTML file. That HTML, *when opened in a browser*,
  loads fonts from Google Fonts and Chart.js from a public CDN
  (`cdn.jsdelivr.net`); the CDN URLs are editable at the top of `report.py` and
  can be pointed at self-hosted copies. No measurement data leaves your machine.

### Things worth reporting

- Command/shell injection via host, port, or other user-supplied arguments
- Path traversal or unsafe file handling in CSV logging or report generation
- HTML/JS injection in the generated report from attacker-controlled CSV content
- Any way the tool sends data to an unexpected destination

### Out of scope

- The intentionally disabled TLS validation in HTTP latency mode (documented above)
- The optional external CDN/font fetches performed by the *browser* when opening
  a generated report
- Denial of service against a host you are deliberately probing
- Issues requiring an already-compromised local machine or malicious local user

## Disclosure

Once a fix is available, we will note it in the commit history and, where
appropriate, publish a GitHub Security Advisory crediting the reporter (unless
you prefer to remain anonymous).
