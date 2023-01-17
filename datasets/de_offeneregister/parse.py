import bz2
import re
from functools import lru_cache
from typing import Any, Optional

import orjson
from fingerprints import generate as fp
from followthemoney.util import join_text, make_entity_id
from nomenklatura.entity import CE
from zavod import Zavod, init_context

URL = "https://daten.offeneregister.de/de_companies_ocdata.jsonl.bz2"

SCHEMES = {
    "HRA": ("Company", "Unternehmen"),
    "HRB": ("Company", "Kapitalgesellschaft"),
    "VR": ("Organization", "Verein"),
    "PR": ("Organization", "Partnerschaft (Personengesellschaft)"),
    "GnR": ("Organization", "Genossenschaft"),
}

REL_SCHEMS = {
    "Geschäftsführer": "Directorship",
    "Inhaber": "Ownership",
    "Liquidator": "Directorship",
    "Persönlich haftender Gesellschafter": "Ownership",
    "Prokurist": "Directorship",
    "Vorstand": "Directorship",
}

MAPPING = {
    "firstname": "firstName",
    "lastname": "lastName",
    "city": "address",
    "start_date": "startDate",
    "end_date": "endDate",
    "position": "role",
    "flag": "description",
}


RE_PATTERNS = (
    re.compile(
        r"(?P<name>.*),\s(?P<city>[\w\s-]+)\s\([\w]+gericht\s(?P<reg>.+)\s(?P<reg_type>(HRA|HRB|VR|PR|GnR))\s(?P<reg_nr>[\d]+)\),?\s?(?P<summary>.*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<name>.*)\s\([\w]+gericht\s(?P<reg>.+)\s(?P<reg_type>(HRA|HRB|VR|PR|GnR))\s(?P<reg_nr>[\d]+)\),?\s?(?P<summary>.*)",
        re.IGNORECASE,
    ),
)


@lru_cache
def parse_officer_company_name(name: str) -> dict[str, Any]:
    """
    examples:

    HA Invest GmbH, Hamburg (Amtsgericht Hamburg HRB 125617).

    VGHW Verwaltungsgesellschaft Hamburg Wandsbek mbH, Hamburg (Amtsgericht Hamburg HRB 139379), Die jeweiligen Geschäftsführer des persönlich haftenden Gesellschafters sind befugt, im Namen der Gesellschaft mit sich im eigenen Namen oder als Vertreter eines Dritten Rechtsgeschäfte abzuschließen."
    """
    for pat in RE_PATTERNS:
        m = pat.match(name)
        if m:
            return m.groupdict()


def make_rel(
    context: Zavod,
    company: CE,
    officer: CE,
    data: dict[str, Any],
    summary: Optional[str] = None,
):
    type_ = data.pop("position")
    schema = REL_SCHEMS[type_]
    if schema == "Ownership" and company.schema.is_a("Asset"):
        proxy = context.make(schema)
        proxy.add("owner", officer)
        proxy.add("asset", company)
    else:
        proxy = context.make("Directorship")
        proxy.add("director", officer)
        proxy.add("organization", company)

    proxy.id = context.make_slug("rel", make_entity_id(company.id, officer.id, type_))
    proxy.add("role", type_)
    proxy.add("summary", summary)
    for key, value in data.get("other_attributes", {}).items():
        if key in MAPPING:
            proxy.add(MAPPING[key], value, quiet=True)

    context.emit(proxy)


def make_officer_and_rel(context: Zavod, company: CE, data: dict[str, Any]):
    type_ = data.pop("type")
    name = data.pop("name")
    rel_summary = None
    if type_ == "company":
        proxy = context.make("Company")
        parsed_data = parse_officer_company_name(name)
        if parsed_data:
            proxy.add("name", parsed_data.pop("name"))
            proxy.add("address", parsed_data.pop("city", None))
            reg = (
                parsed_data.pop("reg"),
                parsed_data.pop("reg_type"),
                parsed_data.pop("reg_nr"),
            )
            proxy.add("registrationNumber", join_text(*reg))
            proxy.add("registrationNumber", join_text(*reg[1:]))
            proxy.id = context.make_slug(*reg)
            rel_summary = parsed_data.pop("summary")
        else:
            proxy.add("name", name)
            proxy.id = context.make_slug(
                "officer", make_entity_id(company.id, fp(name))
            )
    elif type_ == "person":
        proxy = context.make("Person")
        proxy.add("name", name)
        proxy.id = context.make_slug("officer", make_entity_id(company.id, fp(name)))
    else:
        context.log.warning("Unknown type: %s" % type_)
        proxy = context.make("LegalEntity")
        proxy.add("name", name)
        proxy.id = context.make_slug("officer", make_entity_id(company.id, fp(name)))

    for key, value in data.get("other_attributes", {}).items():
        if key in MAPPING:
            proxy.add(MAPPING[key], value, quiet=True)

    make_rel(context, company, proxy, data, rel_summary)
    context.emit(proxy)


def make_company(context: Zavod, data: dict[str, Any]) -> CE:
    meta = data.pop("all_attributes")
    reg_art = meta.pop("_registerArt")
    schema, legalForm = SCHEMES[reg_art]
    proxy = context.make(schema)
    proxy.add("legalForm", legalForm)
    reg_nr = meta.pop("native_company_number")
    proxy.add("registrationNumber", reg_nr)
    proxy.id = context.make_slug(reg_nr)
    # FIXME? better gleif matching:
    proxy.add("registrationNumber", f'{reg_art} {meta.pop("_registerNummer")}')
    proxy.add("status", data.pop("current_status", None))
    oc_id = data.pop("company_number")
    proxy.add("opencorporatesUrl", f"https://opencorporates.com/companies/de/{oc_id}")
    proxy.add("jurisdiction", data.pop("jurisdiction_code"))
    proxy.add("name", data.pop("name"))
    proxy.add("address", data.pop("registered_address", None))
    for name in data.pop("previous_names", []):
        proxy.add("previousName", name.pop("company_name"))
    proxy.add("retrievedAt", data.pop("retrieved_at"))

    context.emit(proxy)
    return proxy


def parse_record(context: Zavod, record: dict[str, Any]):
    company = make_company(context, record)

    for data in record.pop("officers", []):
        make_officer_and_rel(context, company, data)


def parse(context: Zavod):
    data_fp = context.fetch_resource("de_companies_ocdata.jsonl.bz2", URL)
    with bz2.open(data_fp) as f:
        ix = 0
        while True:
            line = f.readline()
            if not line:
                break
            record = orjson.loads(line)
            parse_record(context, record)
            ix += 1
            if ix and ix % 10_000 == 0:
                context.log.info("Parse record %d ..." % ix)
        if ix:
            context.log.info("Parsed %d records." % (ix + 1), fp=data_fp.name)


if __name__ == "__main__":
    with init_context("metadata.yml") as context:
        context.export_metadata("export/index.json")
        parse(context)
