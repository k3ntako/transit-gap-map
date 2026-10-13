# Deploying

The map is static files: `web/` plus the generated `web/data/`. They are
served from a private S3 bucket through CloudFront, which compresses them and
caches them near readers. Expected cost is about $0 a month, with an email
alarm at $5.

**To deploy, once the setup below is done:** GitHub → Actions → **Deploy** →
**Run workflow** on `main`. It rebuilds the data from current sources, runs
`make verify`, uploads, and checks the live site. About 20 minutes. Untick
"rebuild data" to publish only a change to the pages, scripts or styles.

## What is where

| Piece | What it does | Run as | How often |
|---|---|---|---|
| `infra/bootstrap/` | Terraform's state bucket, the `transit-gap-map-deployer` IAM user, the GitHub deploy role, the budget alarm | an **administrator** | once; again only to change permissions |
| `infra/site/` | the site bucket and the CloudFront distribution | the **deployer** | when hosting changes, e.g. adding a domain |
| `scripts/github-setup.sh` | copies the AWS names and the Census key into GitHub | the deployer | after an `infra/site` apply that changes an output |
| `.github/workflows/deploy.yml` | builds, publishes, smoke-tests | GitHub Actions | every deploy |
| `scripts/publish.sh`, `scripts/smoke.sh` | upload and check; what the workflow runs | either | every deploy |

Everything that touches IAM is in `bootstrap`, so the credentials used day to
day cannot grant themselves more access. GitHub holds no AWS key at all: each
run gets credentials good for an hour, and only for runs on `main`.

## First-time setup

You need Terraform 1.10 or newer, the AWS CLI, and `gh` logged in
(`gh auth login`).

### 1. An administrator profile

Used for steps 2 and 3 only:

```bash
aws configure --profile admin        # access key of an admin IAM user
aws configure sso --profile admin    # or IAM Identity Center, if the account uses it
```

### 2. Bootstrap

```bash
cp infra/bootstrap/terraform.tfvars.example infra/bootstrap/terraform.tfvars
# edit it: set budget_email

AWS_PROFILE=admin infra/bootstrap/apply.sh plan    # look first
AWS_PROFILE=admin infra/bootstrap/apply.sh         # apply; it asks before changing anything
```

AWS emails `budget_email` to confirm the alarm subscription. Click the link
or the alarm never arrives.

If the apply fails with `EntityAlreadyExists` on the OIDC provider, another
project in this account already set up GitHub Actions. Uncomment
`create_github_oidc_provider = false` in `terraform.tfvars` and re-run.

### 3. A key for the deployer

```bash
AWS_PROFILE=admin infra/bootstrap/new-key.sh
aws sts get-caller-identity --profile transit-gap-map   # should name ...-deployer
```

This writes the key into a local AWS profile called `transit-gap-map`. It
never appears on screen, in Terraform state or in this repository. The admin
profile is not needed after this.

### 4. The site

```bash
infra/site/apply.sh plan
infra/site/apply.sh          # prints the site's https://….cloudfront.net address
```

A new CloudFront distribution takes a few minutes to come up everywhere.
Until the first publish the address answers 403, because the bucket is empty.

### 5. GitHub

```bash
scripts/github-setup.sh      # reads .env for the Census key
```

From a worktree without its own `.env`, point it at one:
`ENV_FILE=/path/to/checkout/.env scripts/github-setup.sh`.

### 6. First deploy

Run the workflow as above. Or, from a laptop with `make all` already done:

```bash
make publish                 # scripts/publish.sh, then scripts/smoke.sh
```

## Deploying from a laptop

The same scripts the workflow runs, as the deployer profile:

```bash
make all && make publish     # data and pages
make publish-code            # pages, scripts and styles only
make smoke                   # just check the live site
```

On a machine that has never run `infra/site/apply.sh`, run
`infra/site/apply.sh init` first; it connects to the state without changing
anything, which is where the scripts read the bucket and distribution from.

## How a deploy stays consistent

- **Nothing changes for readers until the last step.** CloudFront keeps every
  file until told otherwise, so the upload is invisible; `publish.sh` then
  clears the cache in one invalidation and the new pages and data appear
  together.
- **Browsers always ask before reusing a file** (`Cache-Control: no-cache`).
  A repeat visit gets a fast "not modified" from the nearest edge, and can
  never show an old map after a deploy.
- **`smoke.sh` checks the result, not the upload.** Every page and script is
  fetched back and compared byte for byte with `web/`; `stats.json` is
  compared too, which proves this build's data is what is being served; and
  each GeoJSON file must arrive compressed. It also prints the size readers
  download beside the budget `config.py` sets.
- **The library versions are pinned** in `constraints.txt`, which
  `make setup` uses here and in CI, so CI builds with what the laptop does.
  After an upgrade: `make lock`, `make verify`, commit.

## Rotating the deployer's key

```bash
AWS_PROFILE=transit-gap-map infra/bootstrap/new-key.sh --rotate
```

It makes a new key, checks it works, then deletes the old one. If the key is
lost entirely, do the same with `AWS_PROFILE=admin`.

## Putting back an earlier version

Every workflow run that rebuilt data keeps it as an artifact for 90 days
(`web-data-<run number>` on the run's page). Download it, unzip it into
`web/data/`, check out the matching commit, and `make publish`.

Terraform state is in `s3://transit-gap-map-tfstate-<account>` with
versioning, so an earlier state can be restored from the S3 console under
"Show versions". Old versions are kept for a year.

## Adding a custom domain later

Not built yet. The steps, in order, so that nothing is applied with
permissions it does not have:

1. **Bootstrap grants the deployer** `acm:*` (certificates, us-east-1) and,
   if the domain's DNS is on Route 53, `route53:ChangeResourceRecordSets` and
   `route53:GetChange` on that hosted zone plus `route53:ListHostedZones`.
   Applied as admin.
2. **Site adds** an `aws_acm_certificate` with DNS validation, the
   validation records, `aliases = [domain]` on the distribution, and a
   `viewer_certificate` using the certificate (`sni-only`,
   `TLSv1.2_2021`), then an alias record pointing the domain at the
   distribution. As a variable that defaults to no domain, so the
   `cloudfront.net` address keeps working beside it.
3. **`scripts/github-setup.sh`**, so `SITE_URL` becomes the new address.

Costs: the domain itself, plus $0.50 a month if its DNS is a Route 53 hosted
zone. The certificate is free.
