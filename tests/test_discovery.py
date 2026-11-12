"""Exercise provider request contracts without credentials or network calls."""
import unittest
from unittest.mock import patch

import httpx

from moving_agent import discovery


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        discovery.clear_cache()
    @patch("moving_agent.discovery.httpx.request")
    def test_places_uses_category_and_returns_businesses(self, request):
        place = {"displayName": {"text": "Example Storage"}, "formattedAddress": "San Francisco, CA"}
        request.return_value = httpx.Response(200, json={"places": [place]},
                                              request=httpx.Request("POST", discovery.PLACES_URL))
        self.assertEqual(discovery.businesses("94103", "storage", "test"), [place])
        self.assertEqual(request.call_args.kwargs["json"]["textQuery"], "self storage facilities near 94103, USA")

    @patch("moving_agent.discovery.httpx.request")
    def test_rentcast_maps_active_listings_and_filters(self, request):
        request.return_value = httpx.Response(200, json=[{
            "id": "123", "formattedAddress": "123 Example St, San Francisco, CA",
            "price": 2800, "bedrooms": 0, "listingAgent": {"name": "Example Agent"},
        }], request=httpx.Request("GET", discovery.RENTCAST_URL))
        result = discovery.rentals("test", "San Francisco, CA", max_rent=3000, bedrooms=0)
        self.assertTrue(result["available"])
        self.assertEqual(result["listings"][0]["rent"], 2800)
        self.assertEqual(result["listings"][0]["contact"]["name"], "Example Agent")
        self.assertEqual(request.call_args.kwargs["params"], {
            "city": "San Francisco", "state": "CA", "price": "*:3000",
            "bedrooms": 0, "status": "Active", "limit": 20,
        })

    @patch("moving_agent.discovery.httpx.request", side_effect=httpx.ReadTimeout("timeout"))
    def test_provider_failures_are_distinct_from_empty_results(self, request):
        self.assertIsNone(discovery.businesses("94103", "movers", "test"))
        self.assertFalse(discovery.rentals("test", "94103")["available"])


if __name__ == "__main__":
    unittest.main()
