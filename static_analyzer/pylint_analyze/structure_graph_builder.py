import os
from pathlib import Path
from tqdm import tqdm

from pylint.pyreverse.main import Run as _PyreverseRun

from static_analyzer.pylint_analyze import _banner


class StructureGraphBuilder:

    def __init__(self, root_package, dot_file_prefix, verbose: bool = False):
        self.root_package = root_package
        self.dot_file = dot_file_prefix
        self.verbose = verbose

    def run_pyreverse(self, package: Path) -> None:
        """
        Call pylint’s pyreverse programmatically to obtain structural graphs.
        This produces two .dot files:
            1. classes_<root>.dot    (class diagram)
            2. packages_<root>.dot   (package dependencies)
        The function then just copies/renames the interesting one to *dot_file*.
        """
        _banner("Running pyreverse…", self.verbose)
        try:
            # Equivalent to: pyreverse -o dot   <package>
            _PyreverseRun([str(package), "-o", "dot", "-p", package.name])
        except SystemExit as e:
            # pyreverse calls sys.exit() after finishing.
            if e.code not in (0, None):
                raise

        root_name = Path(package.name).resolve().name.replace("-", "_")
        produced = Path().glob(f"*{root_name}*.dot")
        picked = None
        for f in produced:
            if "classes_" in f.name:
                picked = f
                break
        if picked is None:
            raise RuntimeError("pyreverse did not produce a classes_*.dot file!")

        picked.replace(f"{package.name}_{self.dot_file}")
        _banner(f"Saved structure graph to {self.dot_file}", self.verbose)

    def build(self):
        paths = collect_paths(self.root_package)
        for path in tqdm(paths, desc="Building structure graphs for packages", unit="package"):
            self.run_pyreverse(path)


def collect_paths(root: Path) -> list[Path]:
    collected: list[Path] = []

    def _walk(dir_: Path):
        # Skip tests for now.
        if "tests" in dir_.parts:
            return
        if (dir_ / "__init__.py").is_file():
            collected.append(dir_)
            return
        for child in dir_.iterdir():
            if child.is_dir():
                _walk(child)

    _walk(root)
    return collected
