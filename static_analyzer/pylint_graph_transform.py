import os
from collections import deque
from pathlib import Path
import logging
import pydot
from collections import defaultdict
import re


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
        # for k, v in filtered_call_map.items():
        #     final_msg += f"Method {k} is calling the following methods {', '.join(v)}\n"
        class2class = build_class_to_class_map(result)
        final_msg = format_class_call_map(class2class)

        return result, final_msg


def is_class_method(qualified_name):
    parts = qualified_name.strip('"').split('.')
    if len(parts) < 3:
        return False  # not enough depth: module.class.method
    class_candidate = parts[-2]
    return re.match(r'^[A-Z]', class_candidate) is not None


def extract_class_path(qualified_name):
    parts = qualified_name.strip('"').split('.')
    if len(parts) < 3:
        return None  # not a class method
    class_name = parts[-2]
    if not re.match(r'^[A-Z]', class_name):
        return None
    return '.'.join(parts[:-1])  # module.Class


def build_class_to_class_map(call_map):
    class_calls = defaultdict(set)

    for caller, callees in call_map.items():
        caller_cls = extract_class_path(caller)
        if not caller_cls:
            continue  # skip non-class callers

        for callee in callees:
            callee_cls = extract_class_path(callee)
            if callee_cls:
                class_calls[caller_cls].add(callee_cls)

    return class_calls


def format_class_call_map(class_calls):
    msg = ""
    for caller_cls, callee_classes in class_calls.items():
        msg += f"Class: {caller_cls} calls:\n"
        for callee_cls in sorted(callee_classes):
            msg += f"  - {callee_cls}\n"
    return msg


if __name__ == '__main__':
    a, b = DotGraphTransformer(
        dot_file="/home/ivan/StartUp/CodeBoarding/temp/2a05acf3d46e4cb88289e5c644ca925f/call_graph.dot",
        repo_location="/home/ivan/StartUp/CodeBoarding/repos/django").transform()
    print(len(b))
