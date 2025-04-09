class A():
    def __init__(self, a: int):
        self.a = a

    def __str__(self):
        return f"A({self.a})"

    def __repr__(self):
        return f"A({self.a})"

class B(A):
    def __init__(self, b: str, a: int):
        super().__init__(a)
        self.b = b

    def __str__(self):
        return f"B({self.b})"

    def __repr__(self):
        return f"B({self.b})"

class C(A):
    def __init__(self, c: float, a: int):
        super().__init__(a)
        self.c = c

    def __str__(self):
        return f"C({self.c})"

    def __repr__(self):
        return f"C({self.c})"


def visualize_me():
    a = A(1)
    b = B("test", 2)
    c = C(3.14, 4)

    print(a)
    print(b)
    print(c)

    print(repr(a))
    print(repr(b))
    print(repr(c))
