import os
from collections import deque
from pathlib import Path

import pydot


class DotGraphTransformer:
    def __init__(self, dot_file, repo_location):
        self.dot_file = dot_file
        self.repo = repo_location
        self._load()

    def _load(self):
        # Perform transformation logic here
        (self.G,) = pydot.graph_from_dot_file(self.dot_file)
        self.packages = []
        self.bfs_scan_directory()

    def bfs_scan_directory(self):
        queue = deque()
        queue.append(self.repo)

        while queue:
            current_dir = queue.popleft()
            try:
                entries = os.listdir(current_dir)
            except PermissionError:
                continue

            # Check if this directory has an __init__.py file
            if '__init__.py' in entries and "test" not in current_dir and "tests" not in current_dir:
                package_name = Path(current_dir).name
                self.packages.append(package_name)
                continue

            for entry in entries:
                full_path = os.path.join(current_dir, entry)
                if os.path.isdir(full_path):
                    queue.append(full_path)

    def transform(self):
        # Perform transformation logic here
        result = []
        print(f"Source code packages: {self.packages}")
        for edge in self.G.get_edges():
            src = edge.get_source()
            dst = edge.get_destination()
            attrs = edge.get_attributes()
            skip_entry = False
            for package in self.packages:
                # Check if the package is in the source or destination
                if package.lower() not in src.lower() or package.lower() not in dst.lower():
                    skip_entry = True
                    break
            if skip_entry:
                continue

            edge_s = ""
            edge_s += f"{src} -> {dst}"

            for k, v in attrs.items():
                edge_s += f" [{k}={v}]"
            result.append(edge_s)
        return "\n".join(result)
