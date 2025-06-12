import os
import json
from collections import deque
from pathlib import Path
import logging
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
                logging.warning(f"Permission denied scanning directory: {current_dir!r}")
                continue

            # Check if this directory has an __init__.py file
            if '__init__.py' in entries and "test" not in current_dir and "example" not in current_dir:
                package_name = Path(current_dir).name
                self.packages.append(package_name)
                continue

            for entry in entries:
                full_path = os.path.join(current_dir, entry)
                if os.path.isdir(full_path):
                    queue.append(full_path)

    def transform(self):
        # Perform transformation logic here
        result = {}
        logging.info(f"[Transformer] Source code packages: {self.packages}")
        for edge in self.G.get_edges():
            src = edge.get_source()
            dst = edge.get_destination()
            src_entry = False
            dst_entry = False
            for package in self.packages:
                if package in src:
                    src_entry = True
                if package in dst:
                    dst_entry = True

            if not (src_entry and dst_entry):
                continue
            if src not in result:
                result[src] = [dst]
            else:
                result[src].append(dst)

        final_msg = "The control flow graph looks like:\n"
        for k, v in result.items():
            final_msg += f"Method {k} is calling the following methods {', '.join(v)}\n"
        return result, final_msg
