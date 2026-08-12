import unittest

from agents.cluster_ids import CodeBoardingClusterIds


class TestCodeBoardingClusterIds(unittest.TestCase):
    def test_prefix_for_scope_omits_root_sentinel(self):
        self.assertEqual(CodeBoardingClusterIds.prefix_for_scope("root"), "")
        self.assertEqual(CodeBoardingClusterIds.prefix_for_scope("1.1"), "1.1")

    def test_sort_groups_by_depth_then_uses_natural_order(self):
        self.assertEqual(CodeBoardingClusterIds.sort({"10", "2.1", "3.4", "1", "2"}), ["1", "2", "10", "2.1", "3.4"])

    def test_qualify_local_ids_always_prefixes_non_root_scope(self):
        self.assertEqual(CodeBoardingClusterIds.qualify_local_ids(["1", "2"], "1.1"), ["1.1.1", "1.1.2"])
        self.assertEqual(CodeBoardingClusterIds.qualify_local_ids(["1.1.1"], "1.1"), ["1.1.1.1.1"])


if __name__ == "__main__":
    unittest.main()
