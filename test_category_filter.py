"""Tests for the category filter feature (Issue #2)."""
import pytest
# These tests cover the core logic without needing network access.

# Import the functions we need to test
import sys
sys.path.insert(0, '.')


class TestExtractCategories:
    """Test extract_categories with various Gamma API response shapes."""

    def test_tags_list_of_dicts(self):
        """Gamma API sometimes returns tags as [{slug: 'politics'}, ...]."""
        from main import extract_categories
        info = {
            "tags": [{"slug": "politics"}, {"slug": "US Elections"}]
        }
        cats = extract_categories(info)
        assert "politics" in cats
        assert "us elections" in cats

    def test_tags_list_of_strings(self):
        """Tags might also be plain strings."""
        from main import extract_categories
        info = {
            "tags": ["crypto", "Bitcoin"]
        }
        cats = extract_categories(info)
        assert "crypto" in cats
        assert "bitcoin" in cats

    def test_group_field(self):
        """Group field as a dict."""
        from main import extract_categories
        info = {"group": {"name": "Sports"}}
        cats = extract_categories(info)
        assert "sports" in cats

    def test_group_field_string(self):
        """Group field as a plain string."""
        from main import extract_categories
        info = {"group": "Science"}
        cats = extract_categories(info)
        assert "science" in cats

    def test_category_field(self):
        """Single category string."""
        from main import extract_categories
        info = {"category": "Business"}
        cats = extract_categories(info)
        assert "business" in cats

    def test_multiple_fields_dedup(self):
        """Same category appears in tags and group — should deduplicate."""
        from main import extract_categories
        info = {
            "tags": ["politics"],
            "group": {"name": "politics"}
        }
        cats = extract_categories(info)
        assert cats.count("politics") == 1

    def test_empty_info(self):
        """No category data at all."""
        from main import extract_categories
        assert extract_categories({}) == []

    def test_mixed_tag_types(self):
        """Tags can be mix of dicts and strings."""
        from main import extract_categories
        info = {
            "tags": ["crypto", {"slug": "DeFi"}, {"label": "Bitcoin"}]
        }
        cats = extract_categories(info)
        assert "crypto" in cats
        assert "defi" in cats
        assert "bitcoin" in cats


class TestMatchesFilters:
    """Test the category matching logic."""

    def test_no_filter_allows_all(self):
        from main import matches_filters
        assert matches_filters(["politics"], []) is True
        assert matches_filters([], []) is True

    def test_exact_match(self):
        from main import matches_filters
        assert matches_filters(["politics"], ["politics"]) is True
        assert matches_filters(["crypto"], ["crypto"]) is True

    def test_case_insensitive(self):
        from main import matches_filters
        assert matches_filters(["Politics"], ["politics"]) is True
        assert matches_filters(["CRYPTO"], ["crypto"]) is True

    def test_no_match(self):
        from main import matches_filters
        assert matches_filters(["sports"], ["politics", "crypto"]) is False

    def test_partial_match_not_allowed(self):
        """'pol' should not match 'politics' — must be exact after strip/lower."""
        from main import matches_filters
        assert matches_filters(["pol"], ["politics"]) is False

    def test_multiple_categories_any_match(self):
        """If ANY category matches, return True."""
        from main import matches_filters
        assert matches_filters(
            ["sports", "politics", "tech"],
            ["politics", "crypto"]
        ) is True
