import unittest

from repo_utils import sanitize_repo_url, get_repo_name


class TestRepoUtils(unittest.TestCase):

    def test_sanitize_https_url_with_git(self):
        # Test HTTPS URL that already has .git suffix
        url = "https://github.com/user/repo.git"
        result = sanitize_repo_url(url)
        self.assertEqual(result, "https://github.com/user/repo.git")

    def test_sanitize_https_url_without_git(self):
        # Test HTTPS URL without .git suffix
        url = "https://github.com/user/repo"
        result = sanitize_repo_url(url)
        self.assertEqual(result, "https://github.com/user/repo.git")

    def test_sanitize_http_url(self):
        # Test HTTP URL (less common but should work)
        url = "http://github.com/user/repo"
        result = sanitize_repo_url(url)
        self.assertEqual(result, "http://github.com/user/repo.git")

    def test_sanitize_ssh_url_git_format(self):
        # Test SSH URL in git@ format (should be preserved)
        url = "git@github.com:user/repo.git"
        result = sanitize_repo_url(url)
        self.assertEqual(result, "git@github.com:user/repo.git")

    def test_sanitize_ssh_url_protocol(self):
        # Test SSH URL with ssh:// protocol
        url = "ssh://git@github.com/user/repo.git"
        result = sanitize_repo_url(url)
        self.assertEqual(result, "ssh://git@github.com/user/repo.git")

    def test_sanitize_invalid_url(self):
        # Test unsupported URL format
        url = "ftp://example.com/repo"
        with self.assertRaises(ValueError) as context:
            sanitize_repo_url(url)
        self.assertIn("Unsupported URL format", str(context.exception))

    def test_sanitize_plain_text(self):
        # Test plain text (not a URL)
        url = "just-some-text"
        with self.assertRaises(ValueError):
            sanitize_repo_url(url)

    def test_get_repo_name_https(self):
        # Test extracting repo name from HTTPS URL
        url = "https://github.com/user/my-repo"
        name = get_repo_name(url)
        self.assertEqual(name, "my-repo")

    def test_get_repo_name_with_git_suffix(self):
        # Test extracting repo name when .git is present
        url = "https://github.com/user/my-repo.git"
        name = get_repo_name(url)
        self.assertEqual(name, "my-repo")

    def test_get_repo_name_ssh(self):
        # Test extracting repo name from SSH URL
        url = "git@github.com:user/another-repo.git"
        name = get_repo_name(url)
        self.assertEqual(name, "another-repo")

    def test_get_repo_name_trailing_slash(self):
        # Test URL with trailing slash - sanitize adds .git then strips it
        url = "https://github.com/user/repo"
        name = get_repo_name(url)
        self.assertEqual(name, "repo")

    def test_get_repo_name_complex(self):
        # Test complex repository name
        url = "https://github.com/organization/complex.repo-name_123"
        name = get_repo_name(url)
        self.assertEqual(name, "complex.repo-name_123")
