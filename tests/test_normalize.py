from ysearch import normalize
from ysearch.models import Job


def test_remote_buckets_with_country_hint():
    # JSearch remote rows say just "Anywhere" — country comes from the query.
    assert normalize.location_bucket("Anywhere", remote=True, country_hint="in") == "remote-india"
    assert normalize.location_bucket("Anywhere", remote=True) == "remote-global"


def test_remote_detected_from_location_text():
    assert normalize.location_bucket("Remote - India") == "remote-india"
    assert normalize.location_bucket("Remote-Friendly, United States") == "remote-global"


def test_city_bucket_and_aliases():
    assert normalize.location_bucket("Bengaluru, Karnataka, India") == "bengaluru"
    assert normalize.location_bucket("Bangalore, India") == "bengaluru"  # alias folds
    assert normalize.location_bucket("San Francisco, CA") == "san-francisco"
    assert normalize.location_bucket(None) == "unknown"


def test_dedupe_key_is_company_title_bucket_not_url():
    """The same job from JSearch and the company ATS has different URLs but the
    same key — URL must play no part."""
    a = Job(
        source="jsearch",
        title="AI Product Manager",
        company="Acme AI",
        url="https://in.indeed.com/x",
    )
    b = Job(
        source="greenhouse",
        title="AI Product Manager",
        company="Acme AI!",
        url="https://boards.greenhouse.io/y",
    )
    assert normalize.dedupe_key(a, "remote-india") == normalize.dedupe_key(b, "remote-india")
    assert normalize.dedupe_key(a, "remote-india") == "acme-ai|ai-product-manager|remote-india"


def test_slugify():
    assert normalize.slugify("  Forward-Deployed  Engineer! ") == "forward-deployed-engineer"
    assert normalize.slugify(None) == ""
