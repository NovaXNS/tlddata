# IANA TLD Data

Monthly scrape of [IANA Root Zone Database](https://www.iana.org/domains/root/db).

## How it works

A GitHub Action runs on the 1st of every month (or manually), scrapes every TLD subpage from IANA, and publishes the result as a JSON file attached to a GitHub Release. Only the latest 3 releases are kept.

## Usage

```bash
pip install requests beautifulsoup4
python scraper.py
```

Output: `iana_tlds.json`

## Data structure

Each entry contains:

- `tld` / `domain` — the TLD name
- `type` — generic, country-code, sponsored, infrastructure, etc.
- `manager` — TLD manager name
- `sponsoring_organisation` — full org with address
- `admin_contact` / `tech_contact` — name, email, voice, fax
- `name_servers` — hostnames and IP addresses
- `whois_server` / `rdap_server`
- `registration_url`
- `record_last_updated` / `registration_date`
