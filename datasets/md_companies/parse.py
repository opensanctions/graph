import openpyxl
from lxml import html
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin
from zavod import init_context, Zavod
from nomenklatura.entity import CE


def read_ckan(context: Zavod) -> str:
    if context.dataset.url is None:
        raise RuntimeError("No dataset url")
    path = context.fetch_resource("dataset.html", context.dataset.url)
    with open(path, "r") as fh:
        doc = html.fromstring(fh.read())

    resource_url = None
    for res_anchor in doc.findall('.//li[@class="resource-item"]/a'):
        res_href = res_anchor.get("href", "")
        resource_url = urljoin(context.dataset.url, res_href)

    if resource_url is None:
        raise RuntimeError("No resource URL on data catalog page!")

    path = context.fetch_resource("resource.html", resource_url)
    with open(path, "r") as fh:
        doc = html.fromstring(fh.read())

    for action_anchor in doc.findall('.//div[@class="actions"]//a'):
        return action_anchor.get("href")

    raise RuntimeError("No data URL on data resource page!")


def parse_directors(context: Zavod, company: CE, directors: Optional[str]):
    if directors is None:
        return
    for director in directors.split("],"):
        # if "[" not in director:
        #     print(director, directors)
        #     continue
        role = None
        try:
            director, role = director.rsplit("[", 1)
            role = role.replace("]", "").strip()
        except ValueError:
            pass

        director = director.strip()
        if len(director) < 3:
            continue

        dir = context.make("LegalEntity")
        dir.id = context.make_id(company.id, director)
        dir.add("name", director)
        context.emit(dir)

        dship = context.make("Directorship")
        dship.id = context.make_id("Directorship", company.id, director, role)
        dship.add("organization", company.id)
        dship.add("director", dir.id)
        dship.add("role", role)
        context.emit(dship)


def parse_founders(context: Zavod, company: CE, founders: Optional[str]):
    if founders is None:
        return
    if isinstance(founders, int):
        context.log.warning("last line: %s", founders)
        return
    for founder in founders.split("),"):
        founder = founder.replace(")", "")
        percentage = None
        if "(" in founder:
            founder, percentage = founder.rsplit("(", 1)

        founder = founder.strip()
        found = context.make("LegalEntity")
        found.id = context.make_id(company.id, founder)
        found.add("name", founder)
        context.emit(found)

        own = context.make("Ownership")
        own.id = context.make_id("Ownership", company.id, founder)
        own.add("asset", company.id)
        own.add("owner", found.id)
        own.add("role", percentage)
        context.emit(own)


def parse_owners(context: Zavod, company: CE, owners: Optional[str]):
    if owners is None:
        return
    for owner in owners.split("),"):
        owner = owner.replace(")", "")
        country = None
        if "(" in owner:
            owner, country = owner.rsplit("(", 1)

        owner = owner.strip()
        bo = context.make("LegalEntity")
        bo.id = context.make_id(company.id, owner)
        bo.add("name", owner)
        bo.add("country", country)
        if country is not None and not bo.has("country"):
            context.log.warn("Unknown country code", country=country)
        context.emit(bo)

        own = context.make("Ownership")
        own.id = context.make_id("Ownership", company.id, owner)
        own.add("asset", company.id)
        own.add("owner", bo.id)
        own.add("role", "beneficiarilor efectivi")
        context.emit(own)


def parse_company(context: Zavod, data: Dict[str, Any]):
    idno = data.pop("IDNO/ Cod fiscal")
    name = data.pop("Denumirea completă")
    address = data.pop("Adresa")
    company = context.make("Company")
    if idno is not None:
        company.id = f"oc-companies-md-{idno}"
    else:
        company.id = context.make_id(name, address)
    if company.id is None:
        context.log.error(
            "Cannot generate key",
            idno=idno,
            name=name,
            address=address,
        )
        return
    company.add("name", name)
    company.add("incorporationDate", data.pop("Data înregistrării"))
    company.add("dissolutionDate", data.pop("Data lichidării"))
    company.add("jurisdiction", "md")
    company.add("address", address)
    company.add("legalForm", data.pop("Forma org./jurid."))
    parse_directors(context, company, data.pop("Lista conducătorilor"))
    parse_founders(context, company, data.pop("Lista fondatorilor"))
    parse_owners(context, company, data.pop("Lista beneficiarilor efectivi"))

    # print(data)
    context.emit(company)


def parse_companies(context: Zavod, book: openpyxl.Workbook):
    headers: Optional[List[str]] = None
    for idx, row in enumerate(book["Company"].iter_rows()):
        cells = [c.value for c in row]
        if headers is None:
            if "Denumirea completă" in cells:
                headers = []
                for cell in cells:
                    header = str(cell).split(" (", 1)[0]
                    headers.append(header)
            continue
        data = dict(zip(headers, cells))
        parse_company(context, data)
        if idx > 0 and idx % 10000 == 0:
            context.log.info("Read %d companies..." % idx)


def parse(context: Zavod):
    data_url = read_ckan(context)
    data_path = context.fetch_resource("data.xlsx", data_url)
    # data_path = context.get_resource_path("data.xlsx")
    wb = openpyxl.load_workbook(data_path, read_only=True, data_only=True)
    parse_companies(context, wb)


if __name__ == "__main__":
    with init_context("metadata.yml") as context:
        context.export_metadata("export/index.json")
        parse(context)
