import csv
from typing import Dict
from datetime import datetime
from io import TextIOWrapper
from zipfile import ZipFile
from normality import collapse_spaces
from zavod import Zavod, init_context
from zavod.audit import audit_data

from followthemoney.util import join_text

NAME = "cy_companies"
URL = "https://www.data.gov.cy/node/4016/dataset/download"
TYPES = {"C": "HE", "P": "S", "O": "AE", "N": "BN", "B": "B"}


def parse_date(text):
    if text is None or not len(text.strip()):
        return None
    return datetime.strptime(text, "%d/%m/%Y").date()


def company_id(org_type, reg_nr):
    org_type_oc = TYPES[org_type]
    return f"oc-companies-cy-{org_type_oc}{reg_nr}".lower()


def iter_rows(zip: ZipFile, name: str):
    with zip.open(name, "r") as fh:
        wrapper = TextIOWrapper(fh, encoding="utf-8-sig")
        for row in csv.DictReader(wrapper):
            yield row


def parse_organisations(context: Zavod, rows, addresses: Dict[str, str]):
    for row in rows:
        org_type = row.pop("ORGANISATION_TYPE_CODE")
        reg_nr = row.pop("REGISTRATION_NO")
        if org_type in ("", "Εμπορική Επωνυμία"):
            continue
        entity = context.make("Company")
        entity.id = company_id(org_type, reg_nr)
        entity.add("name", row.pop("ORGANISATION_NAME"))
        entity.add("status", row.pop("ORGANISATION_STATUS"))
        if org_type == "O":
            entity.add("country", "cy")
        else:
            entity.add("jurisdiction", "cy")
        org_type_oc = TYPES[org_type]
        oc_id = f"{org_type_oc}{reg_nr}"
        oc_url = f"https://opencorporates.com/companies/cy/{oc_id}"
        entity.add("opencorporatesUrl", oc_url)
        entity.add("registrationNumber", oc_id)
        entity.add("registrationNumber", f"{org_type}{reg_nr}")
        org_type_text = row.pop("ORGANISATION_TYPE")
        org_subtype = row.pop("ORGANISATION_SUB_TYPE")
        if len(org_subtype.strip()):
            org_type_text = f"{org_type_text} - {org_subtype}"
        entity.add("legalForm", org_type_text)
        reg_date = parse_date(row.pop("REGISTRATION_DATE"))
        entity.add("incorporationDate", reg_date)
        status_date = parse_date(row.pop("ORGANISATION_STATUS_DATE"))
        entity.add("modifiedAt", status_date)

        addr_id = row.pop("ADDRESS_SEQ_NO")
        entity.add("address", addresses.get(addr_id))
        context.emit(entity)
        # print(entity.to_dict())
        audit_data(row, ignore=["NAME_STATUS_CODE", "NAME_STATUS"])


def parse_officials(context: Zavod, rows):
    org_types = list(TYPES.keys())
    for row in rows:
        org_type = row.pop("ORGANISATION_TYPE_CODE")
        if org_type not in org_types:
            continue
        reg_nr = row.pop("REGISTRATION_NO")
        name = row.pop("PERSON_OR_ORGANISATION_NAME")
        position = row.pop("OFFICIAL_POSITION")
        entity = context.make("LegalEntity")
        entity.id = context.make_id(org_type, reg_nr, name)
        entity.add("name", name)
        context.emit(entity)

        link = context.make("Directorship")
        link.id = context.make_id("Directorship", org_type, reg_nr, name, position)
        link.add("organization", company_id(org_type, reg_nr))
        link.add("director", entity.id)
        link.add("role", position)
        context.emit(link)


def load_addresses(rows) -> Dict[str, str]:
    addresses: Dict[str, str] = {}
    for row in rows:
        seq_no = row.pop("ADDRESS_SEQ_NO")
        if seq_no is None:
            continue
        street = row.pop("STREET")
        building = row.pop("BUILDING")
        territory = row.pop("TERRITORY")
        address = join_text(building, street, territory, sep=", ")
        if address is not None:
            address = collapse_spaces(address.replace("_", ""))
            if address is not None:
                addresses[seq_no] = address
    return addresses


def parse(context: Zavod):
    data_path = context.fetch_resource("data.zip", URL)
    with ZipFile(data_path, "r") as zip:
        addresses: Dict[str, str] = {}
        for name in zip.namelist():
            if name.startswith("registered_office_"):
                addresses = load_addresses(iter_rows(zip, name))

        for name in zip.namelist():
            context.log.info("Reading: %s in %s" % (name, data_path))
            if name.startswith("organisations_"):
                rows = iter_rows(zip, name)
                parse_organisations(context, rows, addresses)
            if name.startswith("organisation_officials_"):
                rows = iter_rows(zip, name)
                parse_officials(context, rows)


if __name__ == "__main__":
    with init_context("metadata.yml") as context:
        context.export_metadata("export/index.json")
        parse(context)
