# Security Policy

## Reporting

Please report suspected vulnerabilities privately to the maintainers instead of opening a public issue. Include reproduction steps, affected paths, environment assumptions, and any mitigations you have already tested.

## Sensitive Systems

- Some of the latest agent systems are currently designed around Beton Inspector for security-sensitive access to protected warehouse environments.
- Do not publish real credentials, callback URLs, workspace identifiers, customer data, or private datasets in issues or pull requests.
- Treat `.env.example` as placeholders only. Real integration values must stay out of version control.

## Scope Notes

- Issues involving prompt leakage, unauthorized data access, or bypass of read-only query controls should be treated as security-relevant.
- Standalone direct integration modes are planned in the future and will need separate threat-model review when introduced.
