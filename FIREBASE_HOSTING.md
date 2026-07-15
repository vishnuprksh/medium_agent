# Firebase Hosting Guide

This project is now Firebase-ready:

- Firebase Hosting serves the static public page from `public/`.
- Cloud Functions for Firebase runs the Python digest job on a daily schedule.
- Cloud Firestore stores sent-article history so the digest does not resend old articles.

Firebase Hosting does not run Python processes directly, so the scheduled digest runs as a Cloud Function while Hosting provides the public URL.

## 1. Install the tools

Install the Firebase CLI and sign in:

```bash
npm install -g firebase-tools
firebase login
```

Make sure you have Python 3.10 through 3.13 available locally. The checked-in Firebase config uses the Python 3.13 runtime.

## 2. Create or choose a Firebase project

Create a project in the Firebase console, then attach this repo to it:

```bash
firebase use --add
```

Choose your project and assign it an alias such as `prod`.

Cloud Functions and scheduled jobs require a Firebase project on the Blaze plan. Create a Firestore database in Native mode from the Firebase console if the project does not already have one.

## 3. Configure non-secret environment variables

Create a project-specific environment file for the alias you chose:

```bash
cp .env.firebase.example .env.prod
```

Edit `.env.prod` and set the digest values you want deployed. Keep secrets out of this file. At minimum, review these values:

```text
DIGEST_RECIPIENTS=you@example.com
DIGEST_INTENT=Practical, non-hype articles about building useful AI agents with Python and product thinking.
DIGEST_TAGS=artificial-intelligence, ai-agents, python, software-development
DIGEST_USE_OPENROUTER=true
DIGEST_REQUIRE_DELIVERY_HISTORY=true
DIGEST_HISTORY_COLLECTION=sent_articles
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_FROM=you@example.com
SMTP_USE_TLS=true
APP_USER_AGENT=MediumAIReader/0.1 (+mailto:you@example.com; respectful RSS discovery)
```

If you want to deploy without OpenRouter, set `DIGEST_USE_OPENROUTER=false` and remove `OPENROUTER_API_KEY` from `SECRET_ENV_VARS` in `main.py` before deploying.

## 4. Store secrets in Firebase Secret Manager

Set the secrets used by `main.py`:

```bash
firebase functions:secrets:set SMTP_USERNAME
firebase functions:secrets:set SMTP_PASSWORD
firebase functions:secrets:set OPENROUTER_API_KEY
```

For Gmail SMTP, use a Gmail app password for `SMTP_PASSWORD`.

## 5. Deploy Firestore rules, Functions, and Hosting

Deploy everything from the repo root:

```bash
firebase deploy --only firestore:rules,functions,hosting
```

The first Functions deploy can take several minutes because Firebase builds the Python runtime and installs `requirements.txt`.

## 6. Verify the deployment

Open the Hosting URL printed by the deploy command. It should look like one of these:

```text
https://PROJECT_ID.web.app/
https://PROJECT_ID.firebaseapp.com/
```

Check the scheduled function logs:

```bash
firebase functions:log --only daily_digest
```

To run the digest immediately, open Google Cloud Scheduler for the same project and manually trigger the job named like:

```text
firebase-schedule-daily_digest-us-central1
```

## 7. Change the schedule

The schedule lives in `main.py`:

```python
@scheduler_fn.on_schedule(
    schedule="0 13 * * *",
    timezone=ZoneInfo("Etc/UTC"),
    ...
)
```

Edit the cron expression or timezone, then redeploy:

```bash
firebase deploy --only functions
```

## 8. Optional custom domain

In the Firebase console, open Hosting, add your custom domain, and follow the DNS verification steps. No app code changes are needed.

## References

- Firebase Hosting configuration: https://firebase.google.com/docs/hosting/full-config
- Scheduled Python functions: https://firebase.google.com/docs/functions/schedule-functions
- Function environment variables and secrets: https://firebase.google.com/docs/functions/config-env
