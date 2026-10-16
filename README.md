# Insurance Claims SOP Agent

A chat agent for insurance claims support. It talks naturally but always follows a fixed workflow:

**VERIFY_ID → RESOLVE_INTENT → PROCESS_CASE → POST_PROCESS**

The AI model only understands messages and words the replies. The app's own code decides when you're verified, what claim data can be shown and what happens next, so the model can't skip a step or leak data.

**Live demo:** _URL_

> Demo only: all people and claims are made up. Don't enter real personal information. Emails are simulated. The hosted demo sleeps when idle, so the first visit may take about a minute to load. Please wait for few minutes until the server is up.

## Getting started

1. Open the demo and enter your **OpenAI API key** (Please wait for 5-10 seconds. First time visit takes time to process the key). Usage is billed to your account, typically a few cents per conversation.
   The key is kept only in server memory for your session. It's never logged or saved, and reloading the page forgets it.
2. Paste the sample message from the brief:

   > I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about my denied healthcare claim from January. DOB is 1985-03-15, SSN last four is 4472.

3. Ask follow-up questions about the claim. Answers come from the data in [apps/insurance_claims/fixtures/](apps/insurance_claims/fixtures/).

## How it works

Each message goes through the same steps ([app/workflow/engine.py](app/workflow/engine.py)):

1. The model **extracts** facts from the message (identity details, what the caller wants, claim hints).
2. The facts are **remembered**, even if they belong to a later step.
3. **Rules in code** decide what happens: identity needs 3 matching details, claims are looked up from the data, and off-topic questions are refused.
4. The model **writes the reply** using only the facts it's given.
5. The reply is **checked**: anything that leaks claim data before verification, or isn't backed by the data, is replaced with a safe fixed reply.

## Run locally

Requires Python 3.12.

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 and enter your key. To skip that step, copy `.env.example` to `.env` and set `OPENAI_API_KEY` there. Other settings are explained in `.env.example`.

### Trying your own data

You can edit or add policyholders, claims and guidance in the JSON files in [apps/insurance_claims/fixtures/](apps/insurance_claims/fixtures/), then restart the server. Changes only apply to a local run; the hosted demo uses the data in the repo.

The app checks the files when it starts and stops with a clear error if something is wrong. Keep to the existing format:
- Claim IDs look like `CL-1234`.
- `case_type` is `healthcare`, `dental` or `auto`, and `status` is `denied`, `open` or `closed`.
- Every claim's `party_id` must match a policyholder.

## Limitations

- Consent requests, emails and human handoff are simulated.
- Sessions are kept in memory, so they're lost if the server restarts.
