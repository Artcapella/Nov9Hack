test

## Connected tools

- **Elasticsearch** — connection verified (serverless cluster, v9.7.0). Credentials are in `.env` (gitignored); copy `.env.example` to `.env` and fill in `ELASTICSEARCH_API_KEY` on other machines.
- **Radar (skyhook)** — not yet deployed. Requires `kubectl`/`helm` and access to a Kubernetes cluster, neither of which is available on this machine. Run the Helm install commands from the setup instructions once a cluster is available.
- **Gitdar** — already connected to the repo; it activates automatically once PRs are opened.
- **Dash0** — onboarding link requires an authenticated browser session (redirects to sign-in); open it in a browser and sign in to complete setup.
