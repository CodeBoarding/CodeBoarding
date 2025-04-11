from pycallgraph import PyCallGraph

from static_analyzer.pycallflow_patch.module_tracer import ModuleSyncronousTracer


class ModuleCallGraph(PyCallGraph):
    """
    This class is a wrapper around PyCallGraph to collect the CFG of a specific module.
    It is used to collect the CFG of a specific module.
    """

    def __init__(self, module_name, *args, **kwargs):
        self.module_name = module_name
        super().__init__(*args, **kwargs)

    def reset(self):
        """
        Resets all collected statistics.  This is run automatically by
        start(reset=True) and when the class is initialized.
        """
        self.tracer = ModuleSyncronousTracer(self.module_name, self.output, config=self.config)

        for output in self.output:
            self.prepare_output(output)