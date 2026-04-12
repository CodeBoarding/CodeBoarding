// Intentional dead code so csharp-ls publishes diagnostics that map to
// the unused_code_diagnostics health check categories.
using System;
using System.Collections.Generic;  // CS8019 — unnecessary using
using System.Linq;  // CS8019 — unnecessary using

namespace UnusedCode;

public class Sample
{
    private int neverUsedField = 42;  // CS0414 — assigned but never used
    private int neverAssignedField;  // CS0649 — never assigned

    public void MethodWithUnusedLocal()
    {
        int unusedLocal = 5;  // CS0219 — assigned but never used
        Console.WriteLine("hello");
    }

    public int UnreachableCodeMethod()
    {
        return 1;
        return 2;  // CS0162 — unreachable code
    }
}
