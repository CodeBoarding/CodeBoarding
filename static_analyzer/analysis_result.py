from typing import List

from static_analyzer.graph import Node


class StaticAnalysisResults:
    def __init__(self):
        self.results = {}

    def add_class_hierarchy(self, language, hierarchy):
        """
        Adds a class hierarchy to the results.

        :param class_name: The name of the class.
        :param hierarchy: A list representing the class hierarchy.
        """
        if language not in self.results:
            self.results[language] = {}
        self.results[language]["hierarchy"] = hierarchy

    def add_cfg(self, language, cfg):
        """
        Adds a control flow graph (CFG) to the results.

        :param language: The programming language of the CFG.
        :param cfg: The control flow graph data.
        """
        if language not in self.results:
            self.results[language] = {}
        self.results[language]["cfg"] = cfg

    def add_package_dependencies(self, language, dependencies):
        """
        Adds package dependencies to the results.

        :param language: The programming language of the dependencies.
        :param dependencies: A list of package dependencies.
        """
        if language not in self.results:
            self.results[language] = {}
        self.results[language]["dependencies"] = dependencies

    def add_references(self, language, references: List[Node]):
        """
        Adds source code references to the results.

        :param language: The programming language of the references.
        :param references: A list of source code references.
        """
        if language not in self.results:
            self.results[language] = {}
        # transform references to dict and make the keys lower case so that we can search them case-insensitively
        self.results[language]['references'] = {reference.fully_qualified_name.lower(): reference for reference in
                                                references}

    def get_cfg(self, language):
        """
        Retrieves the control flow graph for a specific language.

        :param language: The programming language of the CFG.
        :return: The control flow graph data or None if not found.
        """
        if language in self.results and "cfg" in self.results[language]:
            return self.results[language]["cfg"]
        raise ValueError(f"Control flow graph for language '{language}' not found in results.")

    def get_hierarchy(self, language) -> dict:
        """
        Retrieves the class hierarchy for a specific language.

        :param language: The programming language of the hierarchy.
        :return: dict {
                        class_qualified_name: {
                            "superclasses": [],
                            "subclasses": [],
                            "file_path": str(file_path),
                            "line_start": start_line,
                            "line_end": end_line }
                    }
        """
        if language in self.results and "hierarchy" in self.results[language]:
            return self.results[language]["hierarchy"]
        raise ValueError(f"Class hierarchy for language '{language}' not found in results.")

    def get_package_dependencies(self, language) -> dict:
        """
        Retrieves the package dependencies for a specific language.

        :param language: The programming language of the dependencies.
        :return: The package dependencies or None if not found.
        """
        if language in self.results and "dependencies" in self.results[language]:
            return self.results[language]["dependencies"]
        raise ValueError(f"Package dependencies for language '{language}' not found in results.")

    def get_reference(self, language, qualified_name) -> Node:
        """
        Retrieves the source code reference for a specific qualified name in a language.

        :param language: The programming language of the reference.
        :param qualified_name: The fully qualified name of the source code element.
        :return: The source code reference or None if not found.
        """
        lower_qn = qualified_name.lower()
        if language in self.results and "references" in self.results[language] and lower_qn in \
                self.results[language]["references"]:
            return self.results[language]["references"][lower_qn]
        # Check if the qualified name is a subset meaning it is a file path:
        if language in self.results and "references" in self.results[language]:
            for ref in self.results[language]["references"].keys():
                if ref.startswith(lower_qn):
                    raise FileExistsError(
                        f"Source code reference for '{qualified_name}' in language '{language}' is a file path, "
                        f"please use the full file path instead of the qualified name.")
        raise ValueError(f"Source code reference for '{qualified_name}' in language '{language}' not found in results.")

    def get_loose_reference(self, language, qualified_name) -> tuple[str, Node]:
        lower_qn = qualified_name.lower()
        if language in self.results and "references" in self.results[language]:
            # Check if the qualified name is a subset of any reference:
            subset_refs = []
            for ref in self.results[language]["references"].keys():
                if ref.endswith(lower_qn):
                    return f"Found a loose match with a fully quantified name: {ref}", \
                        self.results[language]["references"][ref]
                if lower_qn in ref:
                    subset_refs.append(ref)
            if len(subset_refs) != 1:
                return f"Multiple references found for '{qualified_name}' in language: {subset_refs}", \
                    self.results[language]["references"][subset_refs[0]]
            if len(subset_refs) == 1:
                return subset_refs[0], self.results[language]["references"][subset_refs[0]]
        return None, None

    def get_languages(self):
        """
        Retrieves the list of languages for which results are available.

        :return: A list of programming languages.
        """
        return list(self.results.keys())
