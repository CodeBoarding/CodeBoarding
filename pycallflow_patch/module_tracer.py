from pycallgraph.tracer import SyncronousTracer

from pycallflow_patch.module_trace_processor import ModuleTraceProcessor


class ModuleSyncronousTracer(SyncronousTracer):
    def __init__(self, module_name, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Override the default TraceProcessor with ours.
        self.processor = ModuleTraceProcessor(module_name, *args, **kwargs)
