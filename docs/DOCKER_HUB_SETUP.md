# Publishing Shelf to Docker Hub via GitHub Actions

## How It Works

You push commits to `main` as often as you want -- nothing gets published to
Docker Hub until you explicitly create and push a git tag. The workflow only
triggers on tags matching `v*`.

```
commit → commit → commit → git tag v0.1.0-beta.1 → push tag → Docker Hub build
                            ↑ this is the only thing that triggers a publish
```

## One-Time Setup

### 1. Create a Docker Hub account and repository

1. Sign up or log in at https://hub.docker.com
2. Click **Create Repository**
3. Name it `shelf` (so the full image name is `dgahagan/shelf`)
4. Set visibility to **Public** (or Private if you prefer -- free accounts get
   one private repo)

### 2. Create a Docker Hub access token

1. Go to **Account Settings → Security → Access Tokens** (or: My Account →
   Security → New Access Token)
2. Click **New Access Token**
3. Description: `github-actions-shelf`
4. Access permissions: **Read & Write** (not Admin)
5. Click **Generate** and **copy the token immediately** -- you can't see it
   again

### 3. Add secrets to your GitHub repo

1. Go to your GitHub repo: https://github.com/dgahagan/shelf
2. Navigate to **Settings → Secrets and variables → Actions**
3. Click **New repository secret** and add these two secrets:

   | Secret name          | Value                          |
   |----------------------|--------------------------------|
   | `DOCKERHUB_USERNAME` | `dgahagan`                     |
   | `DOCKERHUB_TOKEN`    | The access token from step 2   |

That's it for setup. The workflow file at `.github/workflows/docker-publish.yml`
is already in the repo.

## Publishing a Release

### Tag naming convention

Tags use [semantic versioning](https://semver.org/) with a `v` prefix:

```
v0.1.0-beta.1   ← first beta
v0.1.0-beta.2   ← second beta (fixes to the first)
v0.1.0           ← stable release
v0.2.0-beta.1   ← next feature beta
v0.2.0           ← next stable release
v1.0.0           ← major release
```

### What Docker Hub tags get created

| Git tag             | Docker Hub tags                          |
|---------------------|------------------------------------------|
| `v0.1.0-beta.1`    | `0.1.0-beta.1`, `beta`                   |
| `v0.1.0-beta.2`    | `0.1.0-beta.2`, `beta`                   |
| `v0.1.0`           | `0.1.0`, `0.1`, `latest`                 |
| `v0.2.0`           | `0.2.0`, `0.2`, `latest`                 |

- **`beta`** always points to the most recent beta tag
- **`latest`** always points to the most recent stable (non-prerelease) tag
- Stable tags never overwrite `beta` and vice versa

### Push a beta

```bash
# Make sure your changes are committed and pushed to main first
git tag v0.1.0-beta.1
git push origin v0.1.0-beta.1
```

### Push a stable release

```bash
git tag v0.1.0
git push origin v0.1.0
```

### Manual trigger (optional)

You can also trigger a build from the GitHub UI without creating a tag:

1. Go to **Actions → Publish Docker image → Run workflow**
2. Optionally enter a tag override (e.g., `test`, `nightly`)
3. Click **Run workflow**

This builds from the selected branch's HEAD.

## Pulling the Image

Once published, anyone can pull with:

```bash
# Latest stable
docker pull dgahagan/shelf:latest

# Specific version
docker pull dgahagan/shelf:0.1.0

# Latest beta
docker pull dgahagan/shelf:beta
```

### Using it in docker-compose.yml

To switch from a local build to the published image:

```yaml
services:
  shelf:
    image: dgahagan/shelf:beta    # or :latest, or :0.1.0
    # build: .                    # comment out or remove the build line
    container_name: shelf
    network_mode: host
    environment:
      - CERT_SAN=${CERT_SAN:-DNS:shelf,DNS:localhost}
    volumes:
      - ./data:/data:z
    restart: unless-stopped
```

## Monitoring Builds

- **GitHub Actions tab**: https://github.com/dgahagan/shelf/actions — shows
  build logs, success/failure
- **Docker Hub**: https://hub.docker.com/r/dgahagan/shelf/tags — shows
  published tags and image sizes

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Workflow doesn't trigger | Make sure you pushed the **tag**, not just the commit: `git push origin v0.1.0-beta.1` |
| "denied: requested access to the resource is denied" | Check that `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets are set correctly in GitHub |
| Build fails | Check the Actions tab for build logs — most likely a Dockerfile issue |
| Tag already exists on Docker Hub | Docker Hub tags are overwritten by default (e.g., `beta` and `latest` are rolling). Versioned tags like `0.1.0` should only be used once |
