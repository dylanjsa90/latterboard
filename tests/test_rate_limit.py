import pytest

from app.core.rate_limit import network_of


@pytest.mark.parametrize(
    ("host", "network"),
    [
        ("203.0.113.9", "203.0.113.9"),
        ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:bbbb:cccc:dddd:9", "2001:db8:1:2::/64"),
        ("2001:db8:1:3::1", "2001:db8:1:3::/64"),
        ("::ffff:203.0.113.9", "203.0.113.9"),
        ("testclient", "testclient"),
        (None, "unknown"),
    ],
)
def test_network_of(host: str | None, network: str) -> None:
    assert network_of(host) == network
