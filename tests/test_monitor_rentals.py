import json
import unittest
from dataclasses import replace
from unittest.mock import patch
from urllib.error import HTTPError

from scripts.monitor_rentals import Listing, State, discover, parse_listing


IMMOTOP = "https://www.immotop.lu/annonces/1386319/"
ATHOME = "https://www.athome.lu/location/appartement/walferdange/id-123.html"


def fixture(country="LU", title="Appartement 1 chambre à louer à Walferdange"):
    # Representative structured property and visible feature markup.
    data = {"@context": "https://schema.org", "@type": "Apartment",
            "address": {"addressCountry": country},
            "floorSize": {"value": 40, "unitCode": "MTK"},
            "offers": {"@type": "Offer", "price": 1800, "priceCurrency": "EUR"}}
    return (f'<script type="application/ld+json">{json.dumps(data)}</script>'
            f"<main><h1>{title}</h1><p>Transaction Location</p>"
            "<dl><dt>Garages</dt><dd>1</dd></dl></main>")


class FiltersTest(unittest.TestCase):
    def setUp(self):
        self.listing = Listing(ATHOME, "Appartement 1 chambre", 1800, 40, "LU", True, True)

    def test_inclusive_limits_and_houses(self):
        self.assertTrue(self.listing.matches())
        self.assertTrue(replace(self.listing, title="Maison 2 chambres").matches())

    def test_exclusions(self):
        for changes in [dict(rent=1801), dict(area=39.9), dict(rent=0), dict(rent=None),
                        dict(area=None), dict(country="FR"), dict(country=""),
                        dict(parking=False), dict(rental=False), dict(title="Studio"),
                        dict(title="Appartement studio"), dict(title="Chambre en appartement"),
                        dict(title="Garage")]:
            with self.subTest(changes=changes):
                self.assertFalse(replace(self.listing, **changes).matches())


class ParserTest(unittest.TestCase):
    def test_athome_live_layout_ignores_insurance_price(self):
        data = {"@type": "RealEstateListing", "url": ATHOME,
                "about": {"@type": "Apartment", "numberOfBedrooms": 1,
                          "address": {"addressCountry": "LU"},
                          "floorSize": {"value": 50, "unitCode": "MTK"},
                          "additionalProperty": [{"name": "Indoor parking spaces", "value": 1}]},
                "offers": {"@type": "Offer", "priceCurrency": "EUR",
                           "priceSpecification": {"price": 1550, "priceCurrency": "EUR"}}}
        html = (f'<script type="application/ld+json">{json.dumps(data)}</script>'
                '<main><h1>Appartement 1 chambre à Differdange</h1>'
                '<p>Location 1 550 € Assurez ce bien à partir de 9,15 €/mois</p></main>')
        listing = parse_listing(html, ATHOME)
        self.assertEqual(listing.rent, 1550)
        self.assertTrue(listing.matches())

    def test_athome_structured_property(self):
        listing = parse_listing(fixture(), ATHOME)
        self.assertTrue(listing.matches())
        self.assertEqual((listing.area, listing.rent), (40, 1800))

    def test_foreign_address_is_rejected(self):
        self.assertFalse(parse_listing(fixture(country="BE"), ATHOME).matches())

    def test_immotop_visible_features(self):
        html = ("<main><h1>Appartement 1 chambre neuf</h1><div>€ 1 600/mois</div>"
                "<dl><dt>Transaction</dt><dd>Location</dd><dt>Superficie</dt><dd>60 m²</dd>"
                "<dt>Box, places stationnement</dt><dd>1 place(s) de parking</dd></dl></main>")
        listing = parse_listing(html, IMMOTOP)
        self.assertTrue(listing.matches())
        self.assertEqual(listing.rent, 1600)
        self.assertEqual(listing.area, 60)

    def test_parking_mention_is_not_proof(self):
        html = fixture().replace("<dl><dt>Garages</dt><dd>1</dd></dl>",
                                 "<p>Pas de parking. Parking public à proximité.</p>")
        self.assertFalse(parse_listing(html, ATHOME).parking)

    def test_zero_parking(self):
        self.assertFalse(parse_listing(fixture().replace("<dd>1</dd>", "<dd>0</dd>"), ATHOME).parking)

    def test_sold_listing(self):
        self.assertFalse(parse_listing(fixture(title="Loué • Appartement"), ATHOME).matches())

    def test_zero_bedrooms_excludes_studio(self):
        html = fixture().replace('"@type": "Apartment",', '"@type": "Apartment", "numberOfBedrooms": 0,')
        self.assertFalse(parse_listing(html, ATHOME).matches())

    def test_unknown_athome_country_rejected(self):
        html = '<h1>Appartement à louer à Luxembourg</h1><p>Prix 1500 € Surface 50 m² Garages 1</p>'
        self.assertEqual(parse_listing(html, ATHOME).country, "")


class DiscoveryTest(unittest.TestCase):
    @patch("scripts.monitor_rentals.load")
    def test_pagination_canonicalizes_and_deduplicates(self, load):
        load.side_effect = [
            '<a href="/annonces/1/?ref=search">one</a><a rel="next" href="?page=2">2</a>',
            '<a href="/annonces/1/">one</a><a href="/annonces/2/">two</a>'
            '<a href="https://example.org/annonces/3/">external</a>',
        ]
        self.assertEqual(discover(None, "immotop", "https://www.immotop.lu/search/"),
                         {"https://www.immotop.lu/annonces/1/", "https://www.immotop.lu/annonces/2/"})

    @patch("scripts.monitor_rentals.load", return_value="<html>Access denied</html>")
    def test_empty_or_changed_page_fails(self, load):
        with self.assertRaisesRegex(RuntimeError, "No listing links"):
            discover(None, "immotop", "https://www.immotop.lu/search/")


class StateTest(unittest.TestCase):
    @patch.dict("os.environ", {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_SHA": "abc"})
    @patch("scripts.monitor_rentals.api")
    def test_initializes_branch_and_records(self, api):
        missing = HTTPError("url", 404, "missing", {}, None)
        api.side_effect = [missing, missing, {}, {"content": {"sha": "saved"}}]
        state = State()
        listing = Listing(ATHOME, "Maison", 1500, 60, "LU", True, True)
        state.record(listing)
        self.assertIn(ATHOME, state.seen)
        self.assertEqual(state.sha, "saved")
        self.assertEqual(api.call_args.args[2]["branch"], "rental-monitor-state")

    @patch.dict("os.environ", {"GITHUB_REPOSITORY": "owner/repo"})
    @patch("scripts.monitor_rentals.api")
    def test_permission_error_does_not_reset_history(self, api):
        api.side_effect = HTTPError("url", 403, "forbidden", {}, None)
        with self.assertRaises(HTTPError):
            State()


if __name__ == "__main__":
    unittest.main()
