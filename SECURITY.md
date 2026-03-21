# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Instead, please email **dastonsuman1997@gmail.com** with:

1. A description of the vulnerability
2. Steps to reproduce
3. The potential impact
4. Any suggested fixes (optional)

You should receive an acknowledgment within 48 hours. We'll work with you to understand and address the issue before any public disclosure.

## Scope

This project proxies requests through the Claude Code CLI. Security concerns include:

- **Prompt injection** through the API that could affect CLI behavior
- **Authentication bypass** allowing unauthorized access to the proxy
- **Information leakage** through error messages or logs
- **Denial of service** through resource exhaustion (subprocess spawning)

## Responsible disclosure

We ask that you give us reasonable time to fix vulnerabilities before disclosing them publicly. We're committed to acknowledging and addressing reported issues promptly.
