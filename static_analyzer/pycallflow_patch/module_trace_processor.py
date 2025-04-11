import inspect
import time

from pycallgraph.tracer import TraceProcessor


class ModuleTraceProcessor(TraceProcessor):
    """
    A module tracer that collects information about function calls, however it is aware that we are interested only in
    concrete submodule. It is used to collect the CFG of a specific module.
    The process method is lifted from the parent class with a small patch.
    """

    def __init__(self, module_name, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.module_name = module_name

    def is_traced_module(self, module_name):
        """
        Check if the module name is the one we are interested in. We are more permissive as of now. More doesn't hurt yet.
        """
        return self.module_name.lower() in module_name.lower()

    def process(self, frame, event, arg, memory=None):
        '''This function processes a trace result. Keeps track of
        relationships between calls.
        '''
        if memory is not None and self.previous_event_return:
            # Deal with memory when function has finished so local variables
            # can be cleaned up
            self.previous_event_return = False

            if self.call_stack_memory_out:
                full_name, m = self.call_stack_memory_out.pop(-1)
            else:
                full_name, m = (None, None)

            # NOTE: Call stack is no longer the call stack that may be
            # expected. Potentially need to store a copy of it.
            if full_name and m:
                call_memory = memory - m

                self.func_memory_out[full_name] += call_memory
                self.func_memory_out_max = max(
                    self.func_memory_out_max, self.func_memory_out[full_name]
                )

        if event == 'call':
            keep = True
            code = frame.f_code

            # Stores all the parts of a human readable name of the current call
            full_name_list = []

            # Work out the module name
            module = inspect.getmodule(code)
            if module:
                module_name = module.__name__
                module_path = module.__file__
                if (not self.config.include_stdlib and self.is_module_stdlib(module_path))\
                        and (not self.is_traced_module(module_name)):
                    keep = False

                if module_name == '__main__':
                    module_name = ''
            else:
                module_name = ''

            if module_name:
                full_name_list.append(module_name)

            # Work out the class name
            try:
                class_name = frame.f_locals['self'].__class__.__name__
                full_name_list.append(class_name)
            except (KeyError, AttributeError):
                class_name = ''

            # Work out the current function or method
            func_name = code.co_name
            if func_name == '?':
                func_name = '__main__'
            full_name_list.append(func_name)

            # Create a readable representation of the current call
            full_name = '.'.join(full_name_list)

            if len(self.call_stack) > self.config.max_depth:
                keep = False

            # Load the trace filter, if any. 'keep' determines if we should
            # ignore this call
            if keep and self.config.trace_filter:
                keep = self.config.trace_filter(full_name)

            # Store the call information
            if keep:
                if self.call_stack:
                    src_func = self.call_stack[-1]
                else:
                    src_func = None

                self.call_dict[src_func][full_name] += 1

                self.func_count[full_name] += 1
                self.func_count_max = max(
                    self.func_count_max, self.func_count[full_name]
                )

                self.call_stack.append(full_name)
                self.call_stack_timer.append(time.time())

                if memory is not None:
                    self.call_stack_memory_in.append(memory)
                    self.call_stack_memory_out.append([full_name, memory])

            else:
                self.call_stack.append('')
                self.call_stack_timer.append(None)

        if event == 'return':

            self.previous_event_return = True

            if self.call_stack:
                full_name = self.call_stack.pop(-1)

                if self.call_stack_timer:
                    start_time = self.call_stack_timer.pop(-1)
                else:
                    start_time = None

                if start_time:
                    call_time = time.time() - start_time

                    self.func_time[full_name] += call_time
                    self.func_time_max = max(
                        self.func_time_max, self.func_time[full_name]
                    )

                if memory is not None:
                    if self.call_stack_memory_in:
                        start_mem = self.call_stack_memory_in.pop(-1)
                    else:
                        start_mem = None

                    if start_mem:
                        call_memory = memory - start_mem
                        self.func_memory_in[full_name] += call_memory

                        self.func_memory_in_max = max(
                            self.func_memory_in_max,
                            self.func_memory_in[full_name],
                        )