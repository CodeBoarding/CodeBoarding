# CodeBoarding
CodeBoarding


### Dependencies for static analysis:

Static analysis is based on the PyCallGraph project:

For now it can do, however it has the huge problem that it is not in fact static analysis. It is more of a profiler - meaning you have to run the project.

Problems with static analyzers are:

1. Because Python is dynamically typed (Duck Typed) it is hard to know what is called statically
2. Most existing projects are in fact quite bad - work on just file level (do not explore in depth)

I am skipping over trying harder to make it work with static analysis, as anyway we will probably go to CPP/C, Java or something strongly typed where this should not be an issue.


Good thing for the PyCallGraph project - as in python most stuff are packets you can usually "setup" easily by installing the package and then simply importing it and running the entry point.
This said this is sub-optimal but can be semi-automated. We can check how much time we have and if it is worth exploring something like:
1. User picks a repo
2. We check for it in pypi
3. Install it
4. Generate with LLM entry point call (Alex's work) anyway.
5. Generate the thing

Steps 2 and 3 do NOT seem impossible, however I am sure it will be painful, as most GHub project are not available on Pypi :/. Still for demo I think it is good.