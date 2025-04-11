# class_a.py (entry point)
from class_b import B
from class_c import C

class A:
    def __init__(self):
        self.b = B()
        self.c = C()

    def handle_request(self, user_input):
        print(f"[A] Received input: {user_input}")
        result = self.b.process(user_input)
        self.c.save(result)
        print("[A] Flow complete.")




if __name__ == "__main__":
    a = A()
    a.handle_request("hello world")