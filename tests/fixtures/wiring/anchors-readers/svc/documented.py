class Representation:
    """Call it like this:

    ```python
    import os
    os.environ["DOCUMENTED_ONLY"] = "your-key-here"
    ```

    The line above tells a reader how to use this class. Nothing here reads anything.
    """

    def describe(self) -> str:
        return "nothing is read here"
