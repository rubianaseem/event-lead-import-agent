#!/usr/bin/env python3
"""
Event Lead Import Agent
-----------------------
Turns a messy post-event lead list (badge scans, session attendees, booth notes)
into import-ready files for HubSpot or Salesforce, *before* anything touches the CRM.

  1. Normalises names, emails, phones and companies
  2. Drops junk rows (TBD, blanks, your own booth staff)
  3. Merges in-file duplicates (the "PRIYA SHAH" vs "Priya Shah" problem)
  4. Matches every row against a CRM export: update by Record ID, create new, or hold for review
  5. Maps scan type + booth notes to a campaign member status and an intent flag
  6. Keeps consent as its own column and never invents opt-in
     (and stamps a "Recent Lead Source" column, default "Event", on update + create rows)
  7. Writes an import QA brief + a sales follow-up handoff (Claude if ANTHROPIC_API_KEY is set, template otherwise)

Runs with zero API keys. Usage:
    python event_lead_import_agent.py
    python event_lead_import_agent.py --events my_show.csv --crm hubspot_export.csv --event-name "SaaStr London 2026"
    python event_lead_import_agent.py --lead-source "Conference"   # or --lead-source "" to leave the column out
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# --------------------------------------------------------------------------- #
# Config you will most likely want to change for your own stack
# --------------------------------------------------------------------------- #
PERSONAL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com",
    "hotmail.co.uk", "outlook.com", "live.com", "icloud.com", "me.com",
    "aol.com", "proton.me", "protonmail.com",
}
JUNK_NAMES = {"", "tbd", "tba", "guest", "test", "n/a", "na", "none", "unknown", "-"}
HOT_KEYWORDS = ["demo", "pricing", "price", "trial", "case study", "proposal", "follow up", "follow-up", "budget"]
PARTNER_KEYWORDS = ["partner", "agency", "consultant", "reseller"]

# Column aliases: every event platform names these differently
ALIASES = {
    "name": ["full name", "name", "attendee name", "attendee"],
    "first": ["first name", "firstname", "first"],
    "last": ["last name", "lastname", "surname", "last"],
    "email": ["email address", "email", "e-mail", "work email"],
    "company": ["company", "company name", "organisation", "organization", "account name"],
    "title": ["job title", "title", "role"],
    "phone": ["phone", "phone number", "mobile", "telephone"],
    "notes": ["badge notes", "notes", "comments", "lead notes", "booth notes"],
    "consent": ["opt-in", "opt in", "consent", "marketing consent", "gdpr consent"],
    "scan_type": ["scan type", "source", "lead type", "attendance type", "type"],
}
STATUS_MAP = {  # scan type keyword -> campaign member status
    "booth": "Visited Booth",
    "session": "Attended Session",
    "registered": "Registered",
    "no-show": "No Show",
    "attend": "Attended",
}
# Recent lead source stamped on every update + create row. The header matches a
# HubSpot custom contact property labelled "Recent Lead Source" (internal name
# recent_lead_source) so the import wizard auto-maps it. On Salesforce, map it to a
# custom field (e.g. Recent_Lead_Source__c) or to the standard Lead Source picklist
# on new leads only; see README "Lead source". Set LEAD_SOURCE_VALUE="" to disable.
LEAD_SOURCE_COLUMN = os.getenv("LEAD_SOURCE_COLUMN", "Recent Lead Source")
LEAD_SOURCE_VALUE = os.getenv("LEAD_SOURCE_VALUE", "Event")

COMPANY_SUFFIXES = re.compile(r"[,\s]+(inc\.?|ltd\.?|limited|llc|gmbh|plc|co\.?|corp\.?|s\.?a\.?)$", re.I)
EMAIL_RE = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9.-]+\.[a-z]{2,}$")


@dataclass
class Lead:
    row_num: int
    first: str = ""
    last: str = ""
    title: str = ""
    email: str = ""
    company: str = ""
    phone: str = ""
    notes: list[str] = field(default_factory=list)
    consent: str = "unknown"
    statuses: set[str] = field(default_factory=set)
    flags: list[str] = field(default_factory=list)
    intent: str = "normal"
    action: str = ""  # update | create | review | drop
    record_id: str = ""
    crm_owner: str = ""
    crm_stage: str = ""
    merged_rows: list[int] = field(default_factory=list)

    @property
    def domain(self) -> str:
        return self.email.split("@", 1)[1] if "@" in self.email else ""

    @property
    def full_name(self) -> str:
        return f"{self.first} {self.last}".strip()

    @property
    def status(self) -> str:
        order = ["Attended Session", "Visited Booth", "Attended", "Registered", "No Show"]
        for s in order:
            if s in self.statuses:
                return s
        return "Attended"


# --------------------------------------------------------------------------- #
# Normalisation helpers
# --------------------------------------------------------------------------- #
def pick(row: dict, key: str) -> str:
    lowered = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
    for alias in ALIASES[key]:
        if alias in lowered:
            return lowered[alias].strip()
    return ""


def smart_case(s: str) -> str:
    s = s.strip()
    if s.isupper() or s.islower():
        return " ".join(p.capitalize() for p in s.split())
    return s


def split_name_title(raw: str) -> tuple[str, str, str]:
    """'Priya Shah - Head of Marketing Ops' -> ('Priya', 'Shah', 'Head of Marketing Ops')"""
    title = ""
    parts = re.split(r"\s+[-|–—]\s+|\s*\|\s*", raw.strip(), maxsplit=1)
    name = parts[0]
    if len(parts) > 1:
        title = parts[1].strip()
    name = smart_case(name)
    bits = name.split()
    if not bits:
        return "", "", title
    return bits[0], " ".join(bits[1:]), title


def normalise_phone(raw: str) -> str:
    if not raw:
        return ""
    plus = raw.strip().startswith("+")
    digits = re.sub(r"\D", "", raw)
    if not plus and digits.startswith("07") and len(digits) == 11:  # UK mobile
        return "+44" + digits[1:]
    return ("+" if plus else "") + digits


def normalise_company(raw: str) -> str:
    return COMPANY_SUFFIXES.sub("", raw.strip()).strip()


def company_key(raw: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalise_company(raw).lower())


def parse_consent(raw: str) -> str:
    v = raw.strip().lower()
    if v in {"yes", "y", "true", "1", "opted in", "opt-in"}:
        return "opted_in"
    if v in {"no", "n", "false", "0", "opted out"}:
        return "no_consent"
    return "unknown"


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
def load_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def parse_events(rows: list[dict], internal_domains: set[str]) -> tuple[list[Lead], list[Lead]]:
    kept, dropped = [], []
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        lead = Lead(row_num=i)
        raw_name = pick(row, "name")
        if raw_name:
            lead.first, lead.last, lead.title = split_name_title(raw_name)
        else:
            lead.first, lead.last = smart_case(pick(row, "first")), smart_case(pick(row, "last"))
        lead.title = pick(row, "title") or lead.title
        lead.email = pick(row, "email").strip().lower().replace(" ", "")
        lead.company = normalise_company(pick(row, "company"))
        lead.phone = normalise_phone(pick(row, "phone"))
        note = pick(row, "notes")
        if note:
            lead.notes.append(note)
        lead.consent = parse_consent(pick(row, "consent"))
        scan = pick(row, "scan_type").lower()
        for kw, status in STATUS_MAP.items():
            if kw in scan:
                lead.statuses.add(status)
                break
        lead.merged_rows.append(i)

        # ---- junk / internal ----
        if lead.full_name.lower() in JUNK_NAMES and not lead.email:
            lead.action, lead.flags = "drop", ["junk row (no name, no email)"]
            dropped.append(lead)
            continue
        if lead.domain in internal_domains:
            lead.action, lead.flags = "drop", [f"internal domain ({lead.domain}): booth staff"]
            dropped.append(lead)
            continue
        kept.append(lead)
    return kept, dropped


def merge_duplicates(leads: list[Lead]) -> tuple[list[Lead], int]:
    by_email: dict[str, Lead] = {}
    out, merges = [], 0
    for lead in leads:
        if lead.email and lead.email in by_email:
            base = by_email[lead.email]
            base.title = base.title or lead.title
            base.phone = base.phone or lead.phone
            base.company = base.company if len(base.company) >= len(lead.company) else lead.company
            base.notes += [n for n in lead.notes if n not in base.notes]
            base.statuses |= lead.statuses
            # most restrictive consent wins
            if "no_consent" in (base.consent, lead.consent):
                base.consent = "no_consent"
            elif "opted_in" in (base.consent, lead.consent) and "unknown" not in (base.consent, lead.consent):
                base.consent = "opted_in"
            base.merged_rows += lead.merged_rows
            merges += 1
            continue
        if lead.email:
            by_email[lead.email] = lead
        out.append(lead)
    for lead in out:
        if len(lead.merged_rows) > 1:
            lead.flags.append(f"merged in-file duplicates (rows {', '.join(map(str, lead.merged_rows))})")
    return out, merges


def classify(leads: list[Lead], crm_rows: list[dict]) -> None:
    crm_by_email = {r.get("Email", "").strip().lower(): r for r in crm_rows if r.get("Email")}
    crm_domains: dict[str, list[dict]] = {}
    for r in crm_rows:
        e = r.get("Email", "").strip().lower()
        if "@" in e:
            crm_domains.setdefault(e.split("@", 1)[1], []).append(r)

    for lead in leads:
        notes = " ".join(lead.notes).lower()
        if any(k in notes for k in HOT_KEYWORDS):
            lead.intent = "hot"
            lead.statuses.add("Responded")
        elif any(k in notes for k in PARTNER_KEYWORDS):
            lead.intent = "partner"

        if lead.consent == "unknown":
            lead.flags.append("consent blank: import as non-marketing contact, do not email until confirmed")
        elif lead.consent == "no_consent":
            lead.flags.append("no consent: sales follow-up only, exclude from nurture")

        # invalid email
        if not lead.email or not EMAIL_RE.match(lead.email):
            lead.action = "review"
            hint = ""
            stem = lead.email.split("@", 1)[1] if "@" in lead.email else ""
            for d in crm_domains:
                if stem and d.startswith(stem + "."):
                    hint = f" (CRM has @{d}: likely typo)"
            lead.flags.insert(0, f"invalid email '{lead.email or 'blank'}'{hint}")
            continue

        if lead.domain in PERSONAL_DOMAINS:
            lead.flags.append("personal email: cannot associate to a company by domain")

        hit = crm_by_email.get(lead.email)
        if hit:
            lead.action = "update"
            lead.record_id = hit.get("Record ID", "")
            lead.crm_owner = hit.get("Contact Owner", "")
            lead.crm_stage = hit.get("Lifecycle Stage", "")
            if lead.crm_stage.lower() in {"opportunity", "customer"}:
                lead.flags.append(f"existing {lead.crm_stage}: do NOT overwrite lifecycle stage or lead source")
            continue

        # same domain + same last name = probable duplicate under a different email
        probable = [
            r for r in crm_domains.get(lead.domain, [])
            if lead.last and r.get("Last Name", "").strip().lower() == lead.last.lower()
        ]
        if probable:
            r = probable[0]
            lead.action = "review"
            lead.flags.insert(0, f"probable duplicate of Record {r.get('Record ID')} ({r.get('Email')}): confirm before import")
            continue

        lead.action = "create"
        same_co = crm_domains.get(lead.domain, [])
        if same_co:
            owner = next((r.get("Contact Owner") for r in same_co if r.get("Contact Owner", "Unassigned") != "Unassigned"), "")
            stage = next((r.get("Lifecycle Stage") for r in same_co if r.get("Lifecycle Stage")), "")
            lead.flags.append(
                f"new contact at a known account ({lead.domain}, {stage})" + (f": route to {owner}" if owner else "")
            )
            lead.crm_owner = owner


def write_outputs(leads: list[Lead], dropped: list[Lead], event_name: str, out_dir: Path,
                  lead_source: str = LEAD_SOURCE_VALUE) -> dict[str, Path]:
    out_dir.mkdir(exist_ok=True)
    cols = ["Record ID", "Email", "First Name", "Last Name", "Job Title", "Company", "Phone",
            "Event Name", "Campaign Member Status", "Marketing Consent", "Event Intent", "Event Notes"]
    if lead_source:
        cols.append(LEAD_SOURCE_COLUMN)

    def row(l: Lead) -> dict:
        return {
            "Record ID": l.record_id, "Email": l.email, "First Name": l.first, "Last Name": l.last,
            "Job Title": l.title, "Company": l.company, "Phone": l.phone, "Event Name": event_name,
            "Campaign Member Status": "Responded" if "Responded" in l.statuses else l.status,
            "Marketing Consent": l.consent, "Event Intent": l.intent, "Event Notes": "; ".join(l.notes),
            **({LEAD_SOURCE_COLUMN: lead_source} if lead_source else {}),
        }

    files = {}
    for action in ("update", "create"):
        p = out_dir / f"import_{action}.csv"
        with p.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols if action == "update" else cols[1:])
            w.writeheader()
            for l in leads:
                if l.action == action:
                    r = row(l)
                    if action == "create":
                        r.pop("Record ID")
                    w.writerow(r)
        files[action] = p
    p = out_dir / "needs_review.csv"
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["Source Rows", "Name", "Email", "Company", "Action", "Reason"])
        w.writeheader()
        for l in [x for x in leads if x.action == "review"] + dropped:
            w.writerow({"Source Rows": ",".join(map(str, l.merged_rows)), "Name": l.full_name or "(blank)",
                        "Email": l.email, "Company": l.company, "Action": l.action, "Reason": " | ".join(l.flags)})
    files["review"] = p
    return files


# --------------------------------------------------------------------------- #
# Brief writer: Claude if available, template otherwise
# --------------------------------------------------------------------------- #
def facts_block(leads, dropped, merges, total_rows, event_name) -> str:
    c = Counter(l.action for l in leads)
    lines = [f"Event: {event_name}", f"Raw rows: {total_rows}", f"Dropped: {len(dropped)}",
             f"In-file duplicates merged: {merges}", f"Update existing: {c['update']}",
             f"Create new: {c['create']}", f"Needs review: {c['review']}", "", "Leads:"]
    for l in leads:
        lines.append(
            f"- {l.full_name} | {l.title or 'no title'} | {l.company} | {l.email} | action={l.action} | "
            f"intent={l.intent} | consent={l.consent} | owner={l.crm_owner or 'none'} | "
            f"notes={'; '.join(l.notes) or 'none'} | flags={' / '.join(l.flags) or 'none'}"
        )
    for l in dropped:
        lines.append(f"- DROPPED row {l.row_num}: {' / '.join(l.flags)}")
    return "\n".join(lines)


def template_brief(leads, dropped, merges, total_rows, event_name) -> str:
    c = Counter(l.action for l in leads)
    hot = [l for l in leads if l.intent == "hot"]
    out = [
        f"IMPORT QA BRIEF: {event_name}",
        f"{total_rows} raw rows -> {c['update']} update, {c['create']} create, {c['review']} review, "
        f"{len(dropped)} dropped, {merges} in-file duplicate(s) merged.",
        "",
        "Before you import:",
    ]
    if c["review"]:
        out.append(f"  1. Clear needs_review.csv first ({c['review']} row(s)). These are the ones that become duplicates.")
    out.append("  2. Import import_update.csv (Create and update, match on Email). Don't map Lifecycle Stage / original Lead Source.")
    out.append("  3. Import import_create.csv the same way (match on Email), then spot-check 5 rows.")
    unknown = sum(1 for l in leads if l.consent == "unknown")
    if unknown:
        out.append(f"  4. {unknown} contact(s) have blank consent. Set as non-marketing until confirmed.")
    out += ["", "SALES FOLLOW-UP HANDOFF (hot leads)"]
    if not hot:
        out.append("  No hot leads flagged from booth notes.")
    for l in hot:
        owner = l.crm_owner or "unassigned: route via your normal rules"
        out.append(f"  - {l.full_name} ({l.title or 'title unknown'}, {l.company}) -> {owner}")
        out.append(f"    Context: \"{'; '.join(l.notes)}\". Follow up within 48h referencing that conversation.")
    return "\n".join(out)


def llm_brief(facts: str) -> str | None:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    try:
        import anthropic
    except ImportError:
        print("[info] ANTHROPIC_API_KEY is set but the 'anthropic' package isn't installed. Using template.")
        return None
    prompt = (
        "You are a Marketing Ops lead reviewing a post-event lead import before it goes into the CRM.\n"
        "Using ONLY the facts below (never invent numbers, names or context), write:\n"
        "1. IMPORT QA BRIEF: 3-6 bullet checklist of what to fix or watch before importing, most risky first.\n"
        "2. SALES FOLLOW-UP HANDOFF: for each hot lead, one line with owner and a specific opener based on their booth note.\n"
        "Plain text, no markdown headers beyond those two labels, under 250 words.\n\n" + facts
    )
    try:
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:  # network, auth, model name...
        print(f"[warn] Claude call failed ({type(e).__name__}: {e}). Falling back to template.")
        return None


# --------------------------------------------------------------------------- #
def main() -> int:
    here = Path(__file__).parent
    ap = argparse.ArgumentParser(description="Clean, dedupe and route a post-event lead list before CRM import.")
    ap.add_argument("--events", default=str(here / "sample_event_list.csv"), help="raw event lead CSV")
    ap.add_argument("--crm", default=str(here / "sample_crm_contacts.csv"),
                    help="CRM contact export (Record ID, Email, First Name, Last Name, Company, Lifecycle Stage, Contact Owner)")
    ap.add_argument("--event-name", default=os.getenv("EVENT_NAME", "SaaS Ops Summit London 2026"))
    ap.add_argument("--internal-domains", default=os.getenv("INTERNAL_DOMAINS", "ourcompany.com"))
    ap.add_argument("--out", default=str(here / "out"))
    ap.add_argument("--lead-source", default=LEAD_SOURCE_VALUE,
                    help=f"value for the '{LEAD_SOURCE_COLUMN}' column on update + create rows "
                         f"(default: {LEAD_SOURCE_VALUE!r}, from LEAD_SOURCE_VALUE; \"\" to omit)")
    args = ap.parse_args()

    events_path, crm_path = Path(args.events), Path(args.crm)
    if not events_path.exists():
        print(f"[error] events file not found: {events_path}")
        return 1
    raw = load_csv(events_path)
    crm_rows = load_csv(crm_path) if crm_path.exists() else []
    if not crm_rows:
        print("[info] no CRM export found: every valid row will be treated as new.")
    internal = {d.strip().lower() for d in args.internal_domains.split(",") if d.strip()}

    leads, dropped = parse_events(raw, internal)
    leads, merges = merge_duplicates(leads)
    classify(leads, crm_rows)
    files = write_outputs(leads, dropped, args.event_name, Path(args.out), args.lead_source.strip())

    bar = "=" * 72
    print(bar)
    print(f" EVENT LEAD IMPORT AGENT  |  {args.event_name}")
    print(bar)
    icons = {"update": "UPDATE", "create": "CREATE", "review": "REVIEW"}
    for l in leads:
        tag = icons[l.action] + (f" #{l.record_id}" if l.record_id else "")
        hot = "  [HOT]" if l.intent == "hot" else ("  [PARTNER]" if l.intent == "partner" else "")
        print(f"\n[{tag}] {l.full_name or '(no name)'}  <{l.email or 'no email'}>  {l.company}{hot}")
        if l.title:
            print(f"    title: {l.title}")
        for fl in l.flags:
            print(f"    - {fl}")
    for l in dropped:
        print(f"\n[DROP] row {l.row_num}: {' | '.join(l.flags)}")

    facts = facts_block(leads, dropped, merges, len(raw), args.event_name)
    brief = llm_brief(facts)
    source = "Claude" if brief else "template"
    brief = brief or template_brief(leads, dropped, merges, len(raw), args.event_name)
    print("\n" + bar)
    print(f" BRIEF ({source})")
    print(bar)
    print(brief)
    print("\n" + bar)
    print(" FILES")
    for k, p in files.items():
        print(f"  {k:<7} {p}")
    print(bar)
    return 0


if __name__ == "__main__":
    sys.exit(main())
