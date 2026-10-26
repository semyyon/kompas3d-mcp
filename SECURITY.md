# Security Policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting
(Security tab → "Report a vulnerability"). You will get a reply within a few days.

## Things to know

- `kompas_run_script` executes arbitrary Python with the rights of the user running the server.
  Connect the server only to MCP clients and models you trust.
- The server does not open network ports; it speaks MCP over stdio and talks to KOMPAS-3D through local COM.
