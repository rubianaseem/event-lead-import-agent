# 🎪 Event Lead Import Agent

**Turn a messy post-event lead list into clean, deduplicated, CRM-ready import files, before it touches HubSpot or Salesforce.**

Every conference ends the same way. Someone drops a badge-scan CSV in Slack, the columns don't match your properties, half the names have job titles glued on, emails come in mixed case, and the booth staff scanned each other. It gets imported anyway, HubSpot creates "new" contacts that already existed, sales gets a cold list a week late, and Marketing Ops spends the next fortnight merging duplicates.

This agent does the boring, risky part in seconds. It takes the raw event list and your CRM export, then tells you exactly which rows to **update**, which to **create**, which to **hold for review**, and which to **drop**. It also writes a short import QA brief and a sales follow-up handoff for the hot leads.

> Built by Rubia Naseem, GTM Engineer (12+ yrs CRM/RevOps, HubSpot & Salesforce).

---

## 🧠 The pain point (real, recurring, everywhere)

The same pattern comes up again and again in Marketing Ops and HubSpot communities:

- *"Export badge scan CSV → reformat columns → dedupe manually → import → realize half of them got created as net new because of email capitalization issues → fix duplicates → … sales complains leads are cold → repeat at next conference."* ([r/hubspot: HubSpot lead import after conferences is broken](https://www.reddit.com/r/hubspot/comments/1rxpuhz/hubspot_lead_import_after_conferences_is_broken/))
- Ops teams get event lists with *"inconsistent phone formats, names merged with titles"*, and the CRM *"rejects half the rows."* The thread's consensus is that people want a tool that **shows proposed changes for approval instead of silently "fixing" data.** ([r/hubspot: What tools do you use to clean lead lists before a CRM import](https://www.reddit.com/r/hubspot/comments/1r0h63c/what_tools_do_you_use_to_clean_lead_lists_before/))
- *"Importing ill-formatted data from a random spreadsheet is a recipe for incorrect lead scores and assignments,"* so a list-import template and a QA pass are essential. ([Sponge.io: How to Import a Marketing List without Screwing Up Your Data](https://sponge.io/how-to-import-a-marketing-list-without-screwing-up-your-data/))

This agent follows that consensus. It's deterministic where it has to be (matching and dedupe), it uses an LLM only for the human-readable brief, and **it never writes to your CRM**. You review the files, then import them.

---

## ✨ Features

- **Column auto-mapping:** recognises common header variants ("Full Name", "Attendee", "E-mail", "Organisation", "Opt-in", "Scan Type"…)
- **Normalisation:** lowercases and trims emails, splits `Name - Title` / `Name | Title`, fixes ALL-CAPS names, strips `Inc / Ltd / LLC`, and converts UK mobiles to E.164
- **Junk filter:** drops `TBD`, `Guest` and blank rows, plus anyone on your own domain (booth staff scanning each other)
- **In-file dedupe:** merges `PRIYA SHAH` and `Priya.Shah@…` into one record, keeps the richer notes, and applies the *most restrictive* consent
- **CRM matching:**
  - exact email → `UPDATE` with Record ID
  - same domain + same surname under a different email → `REVIEW` (the classic phantom duplicate)
  - new person at a known account → `CREATE`, routed to that account's owner
- **Invalid-email catcher:** spots `name@fernway` and suggests the domain already in your CRM (`@fernway.com`)
- **Lifecycle guardrails:** flags existing Opportunities and Customers so you don't overwrite lifecycle stage or lead source
- **Consent kept separate:** `opted_in` / `no_consent` / `unknown`, and blank consent is **never** treated as opt-in
- **Campaign member status + intent:** scan type maps to `Visited Booth` / `Attended Session`, and booth notes like "wants demo" or "pricing" become `Responded` + `HOT`
- **Recent lead source:** every update and create row gets a `Recent Lead Source` column set to `Event` (change it with `--lead-source` or `LEAD_SOURCE_VALUE`)
- **Three import-ready CSVs:** `import_update.csv`, `import_create.csv`, `needs_review.csv`
- **QA brief + sales handoff:** written by Claude if you add a key, or by a solid template if you don't

---

## 🚀 Quick start

```bash
git clone https://github.com/rubianaseem/event-lead-import-agent.git
cd event-lead-import-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python event_lead_import_agent.py          # runs on the sample data, no API key needed
```

Use your own files:

```bash
python event_lead_import_agent.py \
  --events ~/Downloads/saastr_badge_scans.csv \
  --crm ~/Downloads/hubspot_contacts_export.csv \
  --event-name "SaaStr AI London 2026" \
  --internal-domains yourcompany.com
```

Optional: `cp .env.example .env` and add `ANTHROPIC_API_KEY` so Claude writes the brief. Full walkthrough in [SETUP.md](SETUP.md).

---

## 📟 Example output (sample data, no API key)

```text
========================================================================
 EVENT LEAD IMPORT AGENT  |  SaaS Ops Summit London 2026
========================================================================

[CREATE] Priya Shah  <priya.shah@northwind-analytics.com>  Northwind Analytics  [HOT]
    title: Head of Marketing Ops
    - merged in-file duplicates (rows 2, 6)
    - new contact at a known account (northwind-analytics.com, Customer): route to Jordan Patel

[UPDATE #50101] Tom Becker  <tom.becker@lumen-saas.io>  Lumen SaaS  [HOT]
    - existing Opportunity: do NOT overwrite lifecycle stage or lead source

[CREATE] Aisha Okafor  <aisha.okafor@gmail.com>  Freelance  [PARTNER]
    - no consent: sales follow-up only, exclude from nurture
    - personal email: cannot associate to a company by domain

[REVIEW] Marco Rossi  <marco.rossi@brightpath.co>  Brightpath
    title: VP Revenue
    - probable duplicate of Record 50102 (m.rossi@brightpath.co): confirm before import
    - consent blank: import as non-marketing contact, do not email until confirmed

[REVIEW] Elena Novak  <elena.novak@fernway>  Fernway  [HOT]
    - invalid email 'elena.novak@fernway' (CRM has @fernway.com: likely typo)

[DROP] row 4: junk row (no name, no email)

[DROP] row 8: internal domain (ourcompany.com): booth staff

========================================================================
 BRIEF (template)
========================================================================
IMPORT QA BRIEF: SaaS Ops Summit London 2026
8 raw rows -> 1 update, 2 create, 2 review, 2 dropped, 1 in-file duplicate(s) merged.

Before you import:
  1. Clear needs_review.csv first (2 row(s)). These are the ones that become duplicates.
  2. Import import_update.csv with Record ID as the match key. Untick 'overwrite' for Lifecycle Stage / Lead Source.
  3. Import import_create.csv with Email as the match key, then spot-check 5 rows.
  4. 1 contact(s) have blank consent. Set as non-marketing until confirmed.

SALES FOLLOW-UP HANDOFF (hot leads)
  - Priya Shah (Head of Marketing Ops, Northwind Analytics) -> Jordan Patel
    Context: "wants demo of routing". Follow up within 48h referencing that conversation.
  - Tom Becker (title unknown, Lumen SaaS) -> Jordan Patel
    Context: "talked pricing; follow up Q1". Follow up within 48h referencing that conversation.
  - Elena Novak (title unknown, Fernway) -> unassigned: route via your normal rules
    Context: "wants case study". Follow up within 48h referencing that conversation.

========================================================================
 FILES
  update  ./out/import_update.csv
  create  ./out/import_create.csv
  review  ./out/needs_review.csv
========================================================================
```

---

## 💷 Cost

- **Without an API key:** free. Everything runs locally in plain Python (standard library only).
- **With `ANTHROPIC_API_KEY`:** one Claude call per run to write the brief. For a list this size that's a few thousand tokens, roughly a cent or two at current Sonnet pricing ([check Anthropic's pricing page](https://www.anthropic.com/pricing)). Matching and dedupe never use the LLM, so a 2,000-row list costs about the same as an 8-row one; only the brief grows.

---

## 🔁 Stack swap table

| Layer | In this repo | Swap for |
|---|---|---|
| Event source | `sample_event_list.csv` | Badge-scanner export, Luma / Eventbrite / Hopin / Zoom Webinar attendee export, Goldcast, Bizzabo |
| CRM truth | `sample_crm_contacts.csv` | HubSpot contacts export or API, Salesforce Leads + Contacts report or SOQL |
| Matching | Email → domain + surname | Add fuzzy matching (`rapidfuzz`), Clay enrichment for personal emails, or HubSpot's Record ID lookup |
| LLM | Claude via `anthropic` | OpenAI, Grok, Gemini, or a local model; it's one function (`llm_brief`) |
| Output | Local CSVs + console | HubSpot Imports API, Salesforce Data Import Wizard / Bulk API, a Slack message to the event owner |
| Orchestration | You run it | n8n, Zapier or Make triggered when a file lands in a Drive folder |

---

## 🛠 Customise it for your stack

Everything you'll want to tune is at the top of `event_lead_import_agent.py`:

- **`ALIASES`:** add the exact column headers your event platform exports
- **`STATUS_MAP`:** match your Salesforce Campaign Member Statuses or your HubSpot "Event attendance" property values
- **`HOT_KEYWORDS` / `PARTNER_KEYWORDS`:** the phrases your booth team actually writes ("POC", "RFP", "intro to CFO"…)
- **`PERSONAL_DOMAINS`:** extend this for your markets (e.g. `web.de`, `orange.fr`, `qq.com`)
- **Output columns:** rename them in `write_outputs()` to your internal property names, so the HubSpot import wizard maps them automatically
- **Routing:** `classify()` currently routes new contacts at known accounts to the existing owner. Plug in your round-robin or territory rules there.

### Lead source

`import_update.csv` and `import_create.csv` include a `Recent Lead Source` column (default `Event`). It's a *recent* touch field, kept separate from the original lead source, so it's safe to stamp on existing Opportunities and Customers. The agent still flags those so you don't overwrite their lifecycle stage or original lead source.

| CRM | Where the column lands |
|---|---|
| HubSpot | Create a custom contact property first: label **Recent Lead Source**, internal name `recent_lead_source`, type Dropdown select, with an option whose label and internal value are both `Event`. The header then auto-maps in the import wizard. HubSpot's built-in Original Source / Latest Source properties are set by HubSpot's own tracking, so they aren't the right target. |
| Salesforce | For new leads, map it to the standard **Lead Source** picklist (`LeadSource`); add `Event` as a picklist value if it's not already there. For existing records, map it to a custom field such as `Recent_Lead_Source__c` instead, so you don't overwrite the original Lead Source. |

Use `--lead-source "Conference"` to change the value, or `--lead-source ""` to leave the column out.

If you're on Salesforce, add a `Campaign ID` column to `import_create.csv` and load it from the Campaign's *Manage Members → Add Members - Import File* flow. That sets the campaign and the Member Status in one pass.

---

## 🧭 How this runs today (and what production would add)

I want to be straight about what this is: a solid, working **pre-flight checker**, not a hands-off system.

**Today:**
- **No auto-trigger.** You run it by hand after each event.
- **Prints to the console** and writes three local CSVs. **It never writes to your CRM.** You review the files and import them yourself.
- **No self-learning.** It doesn't remember what you approved or rejected in `needs_review.csv` last time.
- **One-shot per run.** Each list is processed on its own, with no state between events.
- Matching is exact email plus domain-and-surname. It won't catch `Bob` vs `Robert` at a company that uses two domains.

**What production would add:**
- A trigger: a new file in a Drive or S3 folder, or an event-platform webhook, kicks off a run (n8n is my default here)
- Live CRM lookups via the HubSpot or Salesforce API instead of a static export
- A Slack approval step for the `REVIEW` rows, then an auto-import of the approved files through the CRM's import API
- Fuzzy matching and Clay enrichment to resolve personal emails to companies
- A feedback loop, so reviewer decisions update matching rules and keyword lists over time
- An audit log of every create, update and merge for attribution and compliance

---

## 📁 What's in the repo

```
event-lead-import-agent/
├── event_lead_import_agent.py   # the agent
├── sample_event_list.csv        # 8 messy badge-scan rows
├── sample_crm_contacts.csv      # 4-row CRM export to match against
├── requirements.txt
├── .env.example
├── SETUP.md                     # step-by-step setup, real data sources, extending with AI coding tools
└── README.md
```

---

Built by **Rubia Naseem**, GTM Engineer. If you run events and own the CRM afterwards, I'd love to hear which edge cases your lists throw at you.
