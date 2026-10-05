# Security policy

## Supported versions

Security fixes go into the latest release on `main`. Older versions don't get backports.

## Reporting a vulnerability

Please don't open a public issue for a security problem.

Report it privately through GitHub instead: on this repository's **Security** tab, choose
**Report a vulnerability**. Only the maintainers can see the report.

Include what you can of:

- what the problem is and what an attacker could do with it;
- the steps or a request that reproduces it;
- the version (`GET /health/worker` returns it) and how Assay was run (database,
  worker, settings that matter).

You'll get an answer within a week. Once the problem is confirmed, we'll agree on a date to
publish it together with the fix, and credit you unless you'd rather not be named.
