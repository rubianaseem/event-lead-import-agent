# Setup guide: Event Lead Import Agent

This walks you from a fresh clone to running the agent on your own event lists, then shows how to extend it. Expect about 10 minutes.

---

## 1. Create a virtual environment

Requires Python 3.9+.

```bash
git clone https://github.com/rubianaseem/event-lead-import-agent.git
cd event-lead-import-agent

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

Both dependencies are optional. The core agent uses only the Python standard library. `anthropic` is only needed for Claude-written briefs, and `python-dotenv` only for loading `.env`.

---

## 2. Run on the sample data

```bash
python event_lead_import_agent.py
```

You should see 5 leads classified (1 UPDATE, 2 CREATE, 2 REVIEW), 2 dropped rows, a `BRIEF (template)` section, and three files in `./out/`:

| File | What to do with it |
|---|---|
| `out/import_update.csv` | Import with **Record ID** as the match key; it updates existing contacts |
| `out/import_create.csv` | Import with **Email** as the match key; it creates new contacts |
| `out/needs_review.csv` | Fix by hand first. These are the rows that would have become duplicates or failed |

All the flags:

```bash
python event_lead_import_agent.py --help
#  --events             raw event lead CSV
#  --crm                CRM contact export
#  --event-name         stamped on every row (or EVENT_NAME in .env)
#  --internal-domains   comma-separated; rows on these domains are dropped as booth staff
#  --out                output folder (default ./out)
```

---

## 3. Add your Anthropic API key (optional)

```bash
cp .env.example .env
# edit .env and set:
# ANTHROPIC_API_KEY=sk-ant-...
# ANTHROPIC_MODEL=claude-sonnet-4-6   (optional override)
python event_lead_import_agent.py
```

The brief header changes to `BRIEF (Claude)`. If the key is wrong, the network is down or the model name is unavailable, the agent prints a `[warn]` line and falls back to the template, so a run never fails because of the LLM.

Only the summary facts (names, companies, flags, booth notes) go to the API. Matching and dedupe always run locally. If your DPA doesn't allow sending attendee names to a third party, leave the key unset.

---

## 4. Connect real data sources

The agent reads two CSVs. Here's how to produce them from the tools most teams use.

### 4a. The event list

| Source | How to export |
|---|---|
| **Badge scanner / lead retrieval app** (e.g. the organiser's app) | Exhibitor portal → Leads → Export CSV. Keep the notes, consent/opt-in and scan type columns. |
| **Luma** | Event → Guests → Download as CSV (includes approval and check-in status) |
| **Eventbrite** | Manage event → Reports → Attendee summary → Export CSV |
| **Zoom Webinars** | Reports → Webinar → Registration report / Attendee report → CSV |
| **Goldcast / Bizzabo / Hopin** | Event analytics → Attendees → Export |

If your headers differ, add them to `ALIASES` in the script, e.g. `"email": [..., "attendee email"]`.

### 4b. The CRM export

The agent expects these columns: `Record ID, Email, First Name, Last Name, Company, Lifecycle Stage, Contact Owner`.

**HubSpot (UI)**
1. Contacts → create a view (optional: filter to contacts created in the last 3 years to keep it small).
2. Edit columns: Record ID, Email, First Name, Last Name, Company Name, Lifecycle Stage, Contact Owner.
3. Export → CSV. Rename `Company Name` to `Company` if needed.

**HubSpot (API)**: create a Private App with the `crm.objects.contacts.read` scope, then page through
`GET https://api.hubapi.com/crm/v3/objects/contacts?properties=email,firstname,lastname,company,lifecyclestage,hubspot_owner_id&limit=100`
and write the rows to CSV. Map `hs_object_id` to `Record ID`, and resolve `hubspot_owner_id` with `GET /crm/v3/owners`.

**Salesforce**
1. Reports → New Report → *Contacts & Accounts* (and a second one on *Leads*).
2. Columns: Contact ID / Lead ID, Email, First Name, Last Name, Account Name / Company, Lead Status or a lifecycle field, Owner.
3. Export → CSV (Details only). Rename the ID column to `Record ID` and stack both files.
4. Or run SOQL via the CLI: `sf data query --query "SELECT Id, Email, FirstName, LastName, Account.Name, Owner.Name FROM Contact" --result-format csv > crm.csv`

### 4c. Importing the results

- **HubSpot:** Contacts → Import → File from computer → One file, one object → Contacts. For `import_update.csv`, choose *Update existing contacts* and match on **Record ID**. Leave Lifecycle Stage and Lead Source unmapped so you don't overwrite them. Import 10–20 rows first and check them.
- **Salesforce:** create one Campaign per event with the statuses from `STATUS_MAP` (Visited Booth, Attended Session, Responded). Add a `Campaign ID` column to the files, then Campaign → Manage Members → Add Members - Import File → *Add new and update existing*, matching Leads by Email.
- **Marketo:** import into a static list inside the event program and set the program as the Acquisition Program. Emails must be ASCII, which the agent's normaliser already handles for whitespace and case.

---

## 5. Extend it with an AI coding tool

The script is a single file with clear functions (`parse_events`, `merge_duplicates`, `classify`, `write_outputs`, `llm_brief`), so it's easy to hand to an AI coding agent. Open the folder and try prompts like these.

### Cursor
Open the repo, press `Cmd/Ctrl+L`, and `@event_lead_import_agent.py`:
> "Add fuzzy name matching with rapidfuzz inside classify(): if the same domain has a contact whose full name scores >90, send the row to REVIEW. Add a unit test with pytest using the sample CSVs."

### Claude Code
```bash
claude
```
> "Replace the CRM CSV input with a live HubSpot lookup using a HUBSPOT_TOKEN env var (search contacts by email in batches of 100). Keep the CSV path as a fallback when the token isn't set."

### Codex CLI
```bash
codex
```
> "Add a --post-slack flag that sends the brief to a Slack incoming webhook from SLACK_WEBHOOK_URL. Don't add new dependencies; use urllib."

### Grok
Paste the script into Grok (or use it from your editor) and ask:
> "Write an n8n workflow JSON that watches a Google Drive folder for new CSVs, runs this script via an Execute Command node, and posts needs_review.csv to Slack for approval."

Good next extensions: fuzzy matching, Clay enrichment for personal emails, a Slack approval loop, and pushing approved rows through the HubSpot Imports API.

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `[error] events file not found` | Check the `--events` path. Use quotes if it contains spaces. |
| Every row shows as `REVIEW` with `invalid email 'blank'` | Your email column header isn't recognised. Add it to `ALIASES["email"]`. |
| All rows show `CREATE` even though they exist in the CRM | The CRM export needs an `Email` column (exact header). Check for a BOM or a renamed header like `Email Address`. |
| `[info] no CRM export found` | `--crm` path is wrong or the file is empty. The agent still runs, treating everyone as new. |
| `[info] ANTHROPIC_API_KEY is set but the 'anthropic' package isn't installed` | Activate your venv and run `pip install -r requirements.txt`. |
| `[warn] Claude call failed (AuthenticationError…)` | Wrong or expired key. Regenerate it in the Anthropic console. The template brief is used meanwhile. |
| `[warn] Claude call failed (NotFoundError…)` | Model name not available on your account. Set `ANTHROPIC_MODEL` in `.env`. |
| `UnicodeDecodeError` on read | Re-save the CSV as UTF-8 (Excel: *CSV UTF-8 (Comma delimited)*). |
| Booth staff not dropped | Pass `--internal-domains yourcompany.com,yourcompany.io`. |
