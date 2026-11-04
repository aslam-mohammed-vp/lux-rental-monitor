"""Monitor public rental pages; persist successfully notified listing IDs on GitHub."""

import base64
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from urllib.error import HTTPError
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


SEARCHES = {
    "athome": [
        "https://www.athome.lu/location/appartement?price_max=1800&srf_min=40&carparks=1&sort=date_desc",
        "https://www.athome.lu/location/maison?price_max=1800&srf_min=40&carparks=1&sort=date_desc",
    ],
    "immotop": [
        "https://www.immotop.lu/location-maisons-appartements/luxembourg-pays/"
        "?prezzoMassimo=1800&superficieMinima=40&criterio=data&ordine=desc",
    ],
}
LINKS = {"athome": r"/location/[^/]+/[^/]+/id-\d+\.html$",
         "immotop": r"/annonces/\d+/$"}
MAX_PAGES = 30
STATE_BRANCH = "rental-monitor-state"


def number(value):
    if isinstance(value, (int, float)):
        return float(value)
    value = re.sub(r"[\s\u00a0\u202f]", "", str(value))
    # Accept European and English thousands grouping, with optional decimals.
    if re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+(?:,\d+)?", value):
        value = value.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", value):
        value = value.replace(",", "")
    elif re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?", value):
        value = value.replace(",", ".")
    else:
        raise ValueError(f"Invalid numeric value: {value!r}")
    return float(value)


def selected_sources():
    sources = [source.strip() for source in os.environ.get("MONITOR_SOURCES", "athome").split(",")]
    if not sources or any(source not in SEARCHES for source in sources):
        raise ValueError("MONITOR_SOURCES must contain athome and/or immotop, separated by commas")
    return {source: SEARCHES[source] for source in sources}


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


@dataclass
class Listing:
    url: str
    title: str
    rent: float | None
    area: float | None
    country: str
    parking: bool
    rental: bool

    def matches(self):
        title = self.title.casefold()
        return (
            self.rental and self.country.casefold() in {"lu", "luxembourg", "luxemburg"}
            and bool(re.search(r"\b(appartement|apartment|maison|house|duplex|penthouse|villa)\b", title))
            and not re.search(r"\b(studio|chambre|room)\b", re.sub(r"\d+\s+(chambres?|rooms?)\b", "", title))
            and self.rent is not None and 0 < self.rent <= 1800
            and self.area is not None and self.area >= 40 and self.parking
        )


def parse_listing(html, url):
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    title = heading.get_text(" ", strip=True) if heading else ""
    # Remove recommendations, navigation and footer before inspecting features.
    root = soup.find("main") or soup
    for tag in root.select("script:not([type='application/ld+json']), style, nav, footer, aside"):
        tag.decompose()
    text = root.get_text(" ", strip=True)
    nodes = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            nodes.extend(walk(json.loads(tag.string or tag.get_text())))
        except (ValueError, TypeError):
            continue
    # Only use property nodes, never publisher/agency addresses or related offers.
    properties = [n for n in nodes if any(t in {"Apartment", "House", "Residence", "SingleFamilyResidence"}
                  for t in (n.get("@type", []) if isinstance(n.get("@type"), list) else [n.get("@type")]))]
    prop = properties[0] if properties else {}
    listing_node = next((n for n in nodes if n.get("@type") == "RealEstateListing" and n.get("url") == url), {})
    address = prop.get("address", {})
    country = address.get("addressCountry", "") if isinstance(address, dict) else ""
    if isinstance(country, dict):
        country = country.get("name", "")
    # Country-scoped Immotop discovery is authoritative when JSON-LD omits it.
    if not country and urlsplit(url).hostname == "www.immotop.lu":
        country = "LU"
    if not country:
        # atHome also lists neighbouring countries; require a property address.
        address_node = root.select_one('[itemprop="address"]')
        country_node = root.select_one('[itemprop="addressCountry"]')
        if country_node:
            country = country_node.get("content") or country_node.get_text(strip=True)
        elif address_node and re.search(r"\bL-\d{4}\b", address_node.get_text(" ")):
            country = "LU"
    area_value = prop.get("floorSize", {})
    area = None
    if isinstance(area_value, dict) and area_value.get("unitCode", "MTK") in {"MTK", "m2", "m²"}:
        try:
            area = number(area_value["value"])
        except (KeyError, ValueError, TypeError):
            pass
    if area is None:
        match = re.search(r"(?:Superficie|Surface habitable|Surface)\s*:?\s*([\d\s.,]+)\s*m[²2]", text, re.I)
        if not match:
            match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*m²", text)
        area = number(match[1]) if match else None
    rent_match = re.search(r"(?:Loyer(?: mensuel)?|Prix)\s*:?\s*([\d][\d\s.,]*)\s*€", text, re.I)
    if not rent_match:
        rent_match = re.search(r"€\s*([\d\s.,]+)\s*/\s*mois|([\d][\d\s.,]*)\s*€\s*/\s*mois", text)
    rent = number(next(g for g in rent_match.groups() if g)) if rent_match else None
    if listing_node or rent is None:
        offers = listing_node.get("offers", prop.get("offers", []))
        if isinstance(offers, dict):
            offers = [offers]
        for offer in offers:
            if isinstance(offer, dict):
                price = offer.get("priceSpecification", offer)
                if isinstance(price, dict) and price.get("priceCurrency") == "EUR" and "price" in price:
                    rent = number(price["price"])
                    break
    if rent is None:
        meta = soup.select_one('meta[property="product:price:amount"]')
        if meta:
            rent = number(meta["content"])
    # Require an explicit count in the property's feature section, rather than
    # a mention of public parking or optional parking in marketing copy.
    parking = bool(re.search(
        r"(?:Box,? places stationnement|Garages?|Parkings?|Emplacements? (?:intérieurs?|extérieurs?))"
        r"\s*:?\s*[1-9]\d*\b|\b[1-9]\d*\s+(?:place\(s\)|places?) de parking", text, re.I))
    for amenity in prop.get("amenityFeature", []):
        if isinstance(amenity, dict) and re.search(r"parking|garage", amenity.get("name", ""), re.I):
            parking |= amenity.get("value") is True
    for feature in prop.get("additionalProperty", []):
        if isinstance(feature, dict) and re.search(r"parking spaces|garages?", feature.get("name", ""), re.I):
            try:
                parking |= number(feature.get("value", 0)) > 0
            except (TypeError, ValueError):
                pass
    if prop.get("numberOfBedrooms") == 0 or re.search(r"Typologie\s*:?\s*Studio\b", text, re.I):
        title = "Studio " + title
    rental = bool(re.search(r"\b(location|à louer|/mois)\b", text, re.I))
    if re.search(r"\b(vendu|loué|rented|sold)\b", title, re.I):
        rental = False
    return Listing(url, title, rent, area, str(country), parking, rental)


def api(path, method="GET", data=None):
    request = Request("https://api.github.com/" + path, method=method,
                      data=json.dumps(data).encode() if data is not None else None,
                      headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                               "Accept": "application/vnd.github+json", "Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


class State:
    def __init__(self):
        self.repo = os.environ["GITHUB_REPOSITORY"]
        self.path = f"repos/{self.repo}/contents/seen-rentals.json"
        try:
            result = api(self.path + "?ref=" + STATE_BRANCH)
        except HTTPError as error:
            if error.code != 404:
                raise
            # Verify whether the branch exists before creating it.
            try:
                api(f"repos/{self.repo}/git/ref/heads/{STATE_BRANCH}")
            except HTTPError as branch_error:
                if branch_error.code != 404:
                    raise
                api(f"repos/{self.repo}/git/refs", "POST", {
                    "ref": "refs/heads/" + STATE_BRANCH, "sha": os.environ["GITHUB_SHA"]})
            self.sha, self.seen = None, {}
        else:
            self.sha = result["sha"]
            self.seen = json.loads(base64.b64decode(result["content"]))

    def record(self, listing):
        self.seen[listing.url] = asdict(listing)
        payload = {"message": "Remember notified rental", "branch": STATE_BRANCH,
                   "content": base64.b64encode(json.dumps(self.seen, indent=2).encode()).decode()}
        if self.sha:
            payload["sha"] = self.sha
        result = api(self.path, "PUT", payload)
        self.sha = result["content"]["sha"]


def notify(listing):
    topic = quote(os.environ["NTFY_TOPIC"], safe="")
    body = f"{listing.title}\nEUR {listing.rent:g}/month | {listing.area:g} m² | parking/garage\n{listing.url}"
    request = Request("https://ntfy.sh/" + topic, data=body.encode(), headers={
        "Title": "New Luxembourg rental", "Tags": "house", "Click": listing.url,
        "Content-Type": "text/plain; charset=utf-8"})
    with urlopen(request, timeout=30) as response:
        response.read()


def load(page, url):
    time.sleep(1)
    response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    if response is None or response.status >= 400:
        raise RuntimeError(f"HTTP {response.status if response else 'no response'}: {url}")
    html = page.content()
    if re.search(r"captcha-delivery\.com|Please enable JS and disable|Just a moment\.\.\.", html):
        raise RuntimeError(f"Site blocked automated access: {url}")
    return html


def discover(page, source, search):
    visited, listings = set(), set()
    found_links = False
    url = search
    for _ in range(MAX_PAGES):
        visited.add(url)
        soup = BeautifulSoup(load(page, url), "html.parser")
        candidates = None
        if source == "athome":
            data = page.evaluate("window.__INITIAL_STATE__ && window.__INITIAL_STATE__.search.list")
            if not isinstance(data, list):
                raise RuntimeError(f"Missing atHome search data: {url}")
            candidates = set()
            for item in data:
                features = item.get("characteristic", {})
                candidate = Listing("", item.get("propertySubType", ""), item.get("price"),
                                    features.get("property_surface"), item.get("geo", {}).get("country", ""),
                                    any(features.get(k, 0) > 0 for k in ("garage_carpark", "garages", "carparks", "internal_parking")),
                                    item.get("transactionType") == "rent" and not item.get("isSoldProperty"))
                if candidate.matches():
                    candidates.add(str(item["id"]))
        for anchor in soup.select("a[href]"):
            absolute = urljoin(url, anchor["href"])
            parts = urlsplit(absolute)
            if parts.hostname == urlsplit(search).hostname and re.search(LINKS[source], parts.path):
                found_links = True
                if candidates is None or re.search(r"id-(\d+)", parts.path)[1] in candidates:
                    listings.add(urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")))
        next_link = soup.select_one('a[rel="next"], a[aria-label="Next"], a[aria-label="Suivant"]')
        if next_link is None:
            next_link = next((a for a in soup.select("a[href]") if a.get_text(strip=True).casefold() in {"suivant", "suivante", "next", ">"}), None)
        if next_link is None:
            break
        next_url = urljoin(url, next_link["href"])
        if urlsplit(next_url).hostname != urlsplit(search).hostname or next_url in visited:
            raise RuntimeError(f"Invalid pagination at {url}")
        url = next_url
    else:
        print(f"::warning::Search limited to the first {MAX_PAGES} pages: {search}")
    if not found_links:
        raise RuntimeError(f"No listing links found; check access or site markup: {search}")
    return listings


def main():
    if not os.environ.get("NTFY_TOPIC", "").strip():
        raise RuntimeError("Set the NTFY_TOPIC repository secret")
    from playwright.sync_api import sync_playwright

    sources = selected_sources()
    print(f"Enabled sources: {', '.join(sources)}")
    if "immotop" not in sources:
        print("::warning::Immotop coverage disabled: automated access is blocked. "
              "Set MONITOR_SOURCES=athome,immotop to retry it.")
    state = State()
    errors, sent = [], 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(locale="fr-FR")
        for source, searches in sources.items():
            urls = set()
            for search in searches:
                try:
                    urls.update(discover(page, source, search))
                except Exception as error:
                    errors.append(str(error))
            print(f"{source}: discovered {len(urls)} listings")
            for url in sorted(urls):
                if url in state.seen:
                    continue
                try:
                    listing = parse_listing(load(page, url), url)
                    if not listing.title or listing.rent is None or listing.area is None or not listing.country:
                        raise RuntimeError(f"Missing required property fields: {url}")
                except Exception as error:
                    errors.append(str(error))
                    continue
                if listing.matches():
                    # Persist each successful send immediately. Abort on either
                    # failure to avoid sending a backlog without saving state.
                    notify(listing)
                    state.record(listing)
                    sent += 1
        browser.close()
    print(f"Sent {sent} notifications; {len(errors)} errors")
    for error in errors:
        print(f"::error::{error}")
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
